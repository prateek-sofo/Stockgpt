"""Sofo World -> StockGPT news table (read-only).

Reads the resolved news graph from the Sofo World company database (the hub Postgres, or any
replica) through a read-only role, and maps companies to tickers through
`entity_identifiers` (id_type 'ticker', else 'cik' joined to the SEC ticker file).

What it exports (StockGPT `news` contract):
  * news_articles mentioned with salience >= MIN_SALIENCE (or as the subject) of a listed company:
    published_at, ticker, headline (title), body (<=300 char snippet), source (publisher), url
  * events (earnings_release, guidance_change, acquisition, leadership_change, ...) as extra rows,
    headline = event title, source = 'sofo-event:<type>'

Point-in-time: only rows with a real `published_at` are exported. `first_seen` is the crawl time
and is used only when it is *later* than publication would be (never earlier), so the backtest
can never see an article before Sofo World actually had it.

    SOFO_DATABASE_URL=postgresql://<readonly>@127.0.0.1:5434/sofo_world \
      python -m stockgpt.sources.sofo_world --since 2018-01-01
"""
from __future__ import annotations

import argparse
import os

import pandas as pd

from ..config import DATA_DIR

MIN_SALIENCE = 0.5

NEWS_SQL = """
WITH tick AS (
  SELECT DISTINCT ON (entity_id) entity_id, upper(id_value) AS ticker
  FROM entity_identifiers
  WHERE entity_type = 'company' AND id_type = 'ticker'
  ORDER BY entity_id, confidence DESC, last_seen DESC
), cik AS (
  SELECT DISTINCT ON (entity_id) entity_id, ltrim(id_value, '0') AS cik
  FROM entity_identifiers
  WHERE entity_type = 'company' AND id_type = 'cik'
  ORDER BY entity_id, confidence DESC, last_seen DESC
)
SELECT
  -- the earliest moment Sofo World could have known the article
  GREATEST(a.published_at, LEAST(a.first_seen, a.published_at + interval '6 hours')) AS published_at,
  a.published_at AS original_published_at,
  a.first_seen,
  t.ticker, c.cik,
  a.title AS headline, a.snippet AS body,
  coalesce(a.publisher_name, a.publisher_domain) AS source,
  a.url, a.is_press_release, a.cluster_id, m.salience, m.is_subject
FROM news_articles a
JOIN article_mentions m ON m.article_id = a.article_id AND m.entity_type = 'company'
LEFT JOIN tick t ON t.entity_id = m.entity_id
LEFT JOIN cik  c ON c.entity_id = m.entity_id
WHERE a.published_at IS NOT NULL
  AND a.published_at >= %(since)s
  AND (m.is_subject OR m.salience >= %(min_salience)s)
  AND (t.ticker IS NOT NULL OR c.cik IS NOT NULL)
"""

EVENTS_SQL = """
WITH tick AS (
  SELECT DISTINCT ON (entity_id) entity_id, upper(id_value) AS ticker
  FROM entity_identifiers WHERE entity_type = 'company' AND id_type = 'ticker'
  ORDER BY entity_id, confidence DESC, last_seen DESC
), cik AS (
  SELECT DISTINCT ON (entity_id) entity_id, ltrim(id_value, '0') AS cik
  FROM entity_identifiers WHERE entity_type = 'company' AND id_type = 'cik'
  ORDER BY entity_id, confidence DESC, last_seen DESC
)
SELECT
  -- events carry a date, not a time: count them as known at the later of detection and
  -- the end of the event date, so a same-day event is only usable the next morning
  GREATEST(e.detected_at, (e.event_date + 1)::timestamp AT TIME ZONE 'America/New_York') AS published_at,
  t.ticker, c.cik, e.title AS headline, e.event_type,
  'sofo-event:' || e.event_type AS source, e.source_url AS url, e.confidence
FROM events e
LEFT JOIN tick t ON t.entity_id = e.subject_id
LEFT JOIN cik  c ON c.entity_id = e.subject_id
WHERE e.subject_type = 'company' AND e.event_date >= %(since)s
  AND e.article_id IS NULL                      -- article-backed events are already in news
  AND (t.ticker IS NOT NULL OR c.cik IS NOT NULL)
"""


REL_SQL = """
WITH tick AS (
  SELECT DISTINCT ON (entity_id) entity_id, upper(id_value) AS ticker
  FROM entity_identifiers WHERE entity_type = 'company' AND id_type = 'ticker'
  ORDER BY entity_id, confidence DESC, last_seen DESC
), cik AS (
  SELECT DISTINCT ON (entity_id) entity_id, ltrim(id_value, '0') AS cik
  FROM entity_identifiers WHERE entity_type = 'company' AND id_type = 'cik'
  ORDER BY entity_id, confidence DESC, last_seen DESC
)
SELECT ta.ticker, ca.cik, tb.ticker AS other_ticker, cb.cik AS other_cik,
       r.rel_type, r.confidence, r.first_seen, r.valid_to, r.source_id
FROM relationships r
LEFT JOIN tick ta ON ta.entity_id = r.from_id LEFT JOIN cik ca ON ca.entity_id = r.from_id
LEFT JOIN tick tb ON tb.entity_id = r.to_id   LEFT JOIN cik cb ON cb.entity_id = r.to_id
WHERE r.from_type = 'company' AND r.to_type = 'company'
  AND r.rel_type IN ('competitor', 'customer', 'supplier', 'partner')
  AND r.valid_to IS NULL
"""


def _map_cik(df: pd.DataFrame, sec_tickers: pd.DataFrame | None, tcol: str, ccol: str) -> None:
    if sec_tickers is None or ccol not in df:
        return
    first = sec_tickers.drop_duplicates("cik")
    m = first.set_index(first["cik"].astype(str))["ticker"]
    df[tcol] = df[tcol].fillna(df[ccol].astype(str).map(m))


def export_relationships(dsn: str | None = None, dump: str | None = None,
                         sec_tickers: pd.DataFrame | None = None) -> pd.DataFrame:
    """Company-to-company competitor / customer / supplier / partner links, as tickers."""
    if dump:
        r = _read_any(f"{dump.rstrip('/')}/relationships")
        ids = _read_any(f"{dump.rstrip('/')}/entity_identifiers")
        if r.empty or ids.empty:
            return pd.DataFrame()
        ids = ids[ids["entity_type"] == "company"].sort_values("confidence", ascending=False)
        tick = ids[ids["id_type"] == "ticker"].drop_duplicates("entity_id").set_index("entity_id")["id_value"].str.upper()
        cik = ids[ids["id_type"] == "cik"].drop_duplicates("entity_id").set_index("entity_id")["id_value"].str.lstrip("0")
        r = r[(r["from_type"] == "company") & (r["to_type"] == "company")
              & r["rel_type"].isin(["competitor", "customer", "supplier", "partner"]) & r["valid_to"].isna()]
        df = pd.DataFrame({"ticker": r["from_id"].map(tick), "cik": r["from_id"].map(cik),
                           "other_ticker": r["to_id"].map(tick), "other_cik": r["to_id"].map(cik),
                           "rel_type": r["rel_type"], "confidence": r["confidence"], "first_seen": r["first_seen"]})
    else:
        import psycopg
        with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as conn:
            df = pd.read_sql(REL_SQL, conn)
    _map_cik(df, sec_tickers, "ticker", "cik")
    _map_cik(df, sec_tickers, "other_ticker", "other_cik")
    df = df.dropna(subset=["ticker", "other_ticker"])
    return df[df["ticker"] != df["other_ticker"]].drop_duplicates(["ticker", "other_ticker", "rel_type"])


def export(dsn: str, since: str = "2018-01-01", sec_tickers: pd.DataFrame | None = None) -> pd.DataFrame:
    import psycopg  # optional dependency

    with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as conn:
        news = pd.read_sql(NEWS_SQL, conn, params=dict(since=since, min_salience=MIN_SALIENCE))
        events = pd.read_sql(EVENTS_SQL, conn, params=dict(since=since))
    df = pd.concat([news.assign(kind="article"), events.assign(kind="event")], ignore_index=True)
    if sec_tickers is not None:      # fill missing tickers through CIK
        m = sec_tickers.drop_duplicates("cik").set_index(sec_tickers["cik"].astype(str))["ticker"]
        df["ticker"] = df["ticker"].fillna(df["cik"].astype(str).map(m))
    df = df.dropna(subset=["ticker", "published_at", "headline"])
    df["published_at"] = pd.to_datetime(df["published_at"], utc=True)
    # one row per (story cluster, ticker): syndicated wire copy counts once
    df["_key"] = df["cluster_id"].astype(str).where(df["cluster_id"].notna(), df["headline"])
    df = df.sort_values("published_at").drop_duplicates(["_key", "ticker"]).drop(columns="_key")
    return df


def _read_any(uri: str) -> pd.DataFrame:
    """Read every parquet / csv / jsonl(.gz) under a local folder or s3://bucket/prefix."""
    if uri.startswith("s3://"):
        import boto3
        bucket, _, prefix = uri[5:].partition("/")
        s3 = boto3.client("s3")
        keys = [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
                for o in page.get("Contents", [])]
        paths = [f"s3://{bucket}/{k}" for k in keys]
    else:
        from pathlib import Path
        paths = [str(p) for p in sorted(Path(uri).rglob("*")) if p.is_file()]
    frames = []
    for path in paths:
        if path.endswith(".parquet"):
            frames.append(pd.read_parquet(path))
        elif path.endswith((".csv", ".csv.gz")):
            frames.append(pd.read_csv(path))
        elif path.endswith((".jsonl", ".jsonl.gz", ".ndjson")):
            frames.append(pd.read_json(path, lines=True))
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    # psql CSV writes booleans as t/f and may type ids as numbers: normalise
    for col in ("is_subject", "is_press_release"):
        if col in df and df[col].dtype != bool:
            df[col] = df[col].astype(str).str.lower().isin(["t", "true", "1", "1.0"])
    for col in ("id_value", "id_type", "entity_type", "rel_type", "subject_type"):
        if col in df:
            df[col] = df[col].astype(str)
    return df


def export_from_dump(root: str, since: str = "2018-01-01", sec_tickers: pd.DataFrame | None = None) -> pd.DataFrame:
    """Same result as export(), from table dumps under root/<table>/ (local or s3://).

    Accepts either root/sofo_news/ (an already-joined export) or raw dumps of news_articles,
    article_mentions, entity_identifiers and (optional) events.
    """
    root = root.rstrip("/")
    done = _read_any(f"{root}/sofo_news")
    if len(done):
        df = done
    else:
        art = _read_any(f"{root}/news_articles")
        men = _read_any(f"{root}/article_mentions")
        ids = _read_any(f"{root}/entity_identifiers")
        ev = _read_any(f"{root}/events")
        ids = ids[ids["entity_type"] == "company"].sort_values("confidence", ascending=False)
        tick = ids[ids["id_type"] == "ticker"].drop_duplicates("entity_id").set_index("entity_id")["id_value"].str.upper()
        cik = ids[ids["id_type"] == "cik"].drop_duplicates("entity_id").set_index("entity_id")["id_value"].str.lstrip("0")
        for c in ("published_at", "first_seen"):
            art[c] = pd.to_datetime(art[c], utc=True, errors="coerce")
        art = art[art["published_at"].notna() & (art["published_at"] >= pd.Timestamp(since, tz="UTC"))]
        men = men[(men["entity_type"] == "company") & (men["is_subject"] | (men["salience"] >= MIN_SALIENCE))]
        df = men.merge(art, on="article_id")
        df["ticker"], df["cik"] = df["entity_id"].map(tick), df["entity_id"].map(cik)
        late = df["first_seen"].clip(upper=df["published_at"] + pd.Timedelta(hours=6))
        df["published_at"] = df["published_at"].where(late.isna(), pd.concat([df["published_at"], late], axis=1).max(axis=1))
        df = df.rename(columns={"title": "headline", "snippet": "body"})
        df["source"] = df.get("publisher_name", pd.Series(index=df.index)).fillna(df.get("publisher_domain"))
        df["kind"] = "article"
        if len(ev):
            ev = ev[(ev["subject_type"] == "company") & ev["article_id"].isna()].copy()
            ev["event_date"] = pd.to_datetime(ev["event_date"])
            ev = ev[ev["event_date"] >= since]
            next_morning = (ev["event_date"] + pd.Timedelta(days=1)).dt.tz_localize("America/New_York").dt.tz_convert("UTC")
            ev["published_at"] = pd.concat([pd.to_datetime(ev["detected_at"], utc=True), next_morning], axis=1).max(axis=1)
            ev["ticker"], ev["cik"] = ev["subject_id"].map(tick), ev["subject_id"].map(cik)
            ev = ev.rename(columns={"title": "headline", "source_url": "url"})
            ev["source"] = "sofo-event:" + ev["event_type"]
            ev["kind"] = "event"
            df = pd.concat([df, ev], ignore_index=True)
    if sec_tickers is not None:
        m = sec_tickers.drop_duplicates("cik").set_index(sec_tickers.drop_duplicates("cik")["cik"].astype(str))["ticker"]
        df["ticker"] = df["ticker"].fillna(df["cik"].astype(str).map(m))
    df = df.dropna(subset=["ticker", "published_at", "headline"])
    df["published_at"] = pd.to_datetime(df["published_at"], utc=True)
    key = df["cluster_id"].astype(str).where(df["cluster_id"].notna(), df["headline"]) if "cluster_id" in df else df["headline"]
    df = df.assign(_key=key).sort_values("published_at").drop_duplicates(["_key", "ticker"]).drop(columns="_key")
    return df


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2018-01-01")
    ap.add_argument("--dsn", default=os.getenv("SOFO_DATABASE_URL"))
    ap.add_argument("--dump", default=os.getenv("SOFO_NEWS_URI"),
                    help="s3://bucket/prefix or folder holding table dumps (instead of a live DB)")
    a = ap.parse_args()
    sec = DATA_DIR / "sec_tickers.parquet"
    sect = pd.read_parquet(sec) if sec.exists() else None
    if a.dump:
        out = export_from_dump(a.dump, a.since, sect)
    elif a.dsn:
        out = export(a.dsn, a.since, sect)
    else:
        raise SystemExit("set SOFO_NEWS_URI (s3://... dump) or SOFO_DATABASE_URL (read-only role)")
    keep = ["published_at", "ticker", "headline", "body", "source", "url", "kind"]
    out = out[[c for c in keep if c in out]]
    out.to_parquet(DATA_DIR / "sofo_news.parquet", index=False)
    rel = export_relationships(a.dsn if not a.dump else None, a.dump, sect)
    if len(rel):
        rel.to_parquet(DATA_DIR / "sofo_relationships.parquet", index=False)
        print(f"relationships: {len(rel):,} links ({rel['rel_type'].value_counts().to_dict()})")
    print(f"news rows: {len(out):,}  tickers: {out['ticker'].nunique():,}  "
          f"range: {out['published_at'].min()} -> {out['published_at'].max()}")
