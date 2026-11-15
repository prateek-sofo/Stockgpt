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


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2018-01-01")
    ap.add_argument("--dsn", default=os.getenv("SOFO_DATABASE_URL"))
    a = ap.parse_args()
    if not a.dsn:
        raise SystemExit("set SOFO_DATABASE_URL (read-only role) or pass --dsn")
    sec = DATA_DIR / "sec_tickers.parquet"
    out = export(a.dsn, a.since, pd.read_parquet(sec) if sec.exists() else None)
    out.to_parquet(DATA_DIR / "sofo_news.parquet", index=False)
    print(f"news rows: {len(out):,}  tickers: {out['ticker'].nunique():,}  "
          f"range: {out['published_at'].min()} -> {out['published_at'].max()}")
