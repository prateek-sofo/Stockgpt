"""Sofo World over its own API: the connection that works from anywhere (a laptop, a server, a cloud
session) with one URL and one service key.

Sofo World serves bulk, incremental exports at /v1/exports/{news,events,relationships,sec}. Each
page is ordered by the time Sofo World recorded the row; this module remembers the last cursor per
table, so every run pulls only what is new, including an article published days ago and found today.
Rows are kept locally (data/raw/sofo_api/*.jsonl.gz) and reshaped into the tables the model reads.

    SOFO_WORLD_URL=https://<sofo world host>  SOFO_WORLD_KEY=sk_world_...  python -m stockgpt.sources.sofo_api

The key is a Sofo World service key with the `read` scope (Command Center, Settings, API keys).
In a Claude cloud session the key is stored as a network secret for the host and added to requests
by the session's proxy, so SOFO_WORLD_KEY is not needed there.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import time

import httpx
import pandas as pd

from ..config import DATA_DIR
from .sofo_world import MIN_SALIENCE, _dedupe, _map_cik, publish

STORE = DATA_DIR / "sofo_api"
TABLES = ("news", "events", "relationships", "sec")
ET = "America/New_York"


def client(url: str | None = None, key: str | None = None, transport=None) -> httpx.Client:
    url = url or os.environ.get("SOFO_WORLD_URL")
    if not url:
        raise SystemExit("set SOFO_WORLD_URL (and SOFO_WORLD_KEY, a Sofo World service key with the read scope)")
    key = key or os.environ.get("SOFO_WORLD_KEY")
    headers = {"User-Agent": "StockGPT", **({"Authorization": f"Bearer {key}"} if key else {})}
    return httpx.Client(base_url=url.rstrip("/"), headers=headers, timeout=120, transport=transport)


def sync(c: httpx.Client, table: str, since: str = "2018-01-01", limit: int = 2000, max_pages: int = 100000) -> int:
    """Pull every page after the stored cursor and append it to the local copy. Returns rows added."""
    STORE.mkdir(parents=True, exist_ok=True)
    state_f = STORE / "state.json"
    state = json.loads(state_f.read_text()) if state_f.exists() else {}
    cursor, added = state.get(table), 0
    for _ in range(max_pages):
        params = dict(limit=limit, **({"after": cursor} if cursor else {"since": f"{since}T00:00:00+00:00"}))
        for attempt in range(4):
            r = c.get(f"/v1/exports/{table}", params=params)
            if r.status_code not in (429, 502, 503, 504):
                break
            time.sleep(5 * (attempt + 1))
        if r.status_code in (401, 403):
            raise SystemExit(f"Sofo World refused the key ({r.status_code}): {r.text[:200]}")
        if r.status_code == 404:
            raise SystemExit("this Sofo World does not serve /v1/exports yet (deploy the exports route)")
        r.raise_for_status()
        page = r.json()
        if page["rows"]:
            with gzip.open(STORE / f"{table}.jsonl.gz", "at") as f:      # append: concatenated gzip members
                for row in page["rows"]:
                    f.write(json.dumps(row) + "\n")
            added += len(page["rows"])
            cursor = page["next"]
            state[table] = cursor
            state_f.write_text(json.dumps(state, indent=1))             # saved per page: an interrupted run resumes
        if not page.get("more"):
            break
    return added


def load(table: str) -> pd.DataFrame:
    f = STORE / f"{table}.jsonl.gz"
    if not f.exists():
        return pd.DataFrame()
    with gzip.open(f, "rt") as fh:
        return pd.DataFrame([json.loads(line) for line in fh])


def news_table(news: pd.DataFrame, events: pd.DataFrame, sec_tickers: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per (story, ticker) with the earliest time the article could have been acted on."""
    frames = []
    if len(news):
        n = news.drop_duplicates("article_id").explode("mentions").dropna(subset=["mentions"])
        m = pd.DataFrame(n["mentions"].tolist(), index=n.index)
        n = pd.concat([n.drop(columns="mentions"), m], axis=1)
        n = n[n["is_subject"].fillna(False).astype(bool) | (n["salience"].fillna(0) >= MIN_SALIENCE)].copy()
        pub = pd.to_datetime(n["published_at"], utc=True, errors="coerce")
        seen = pd.to_datetime(n["first_seen"], utc=True, errors="coerce")
        # never earlier than publication; if Sofo World found it late, no later than 6 hours after it
        late = seen.clip(upper=pub + pd.Timedelta(hours=6))
        n["published_at"] = pd.concat([pub, late], axis=1).max(axis=1)
        n = n.rename(columns={"title": "headline", "snippet": "body", "publisher": "source"})
        n["kind"] = "article"
        frames.append(n)
    if len(events):
        e = events[events["article_id"].isna()].copy() if "article_id" in events else events.copy()
        day = pd.to_datetime(e["event_date"])
        next_morning = (day + pd.Timedelta(days=1)).dt.tz_localize(ET).dt.tz_convert("UTC")
        # an event has a date, not a time: usable from the later of detection and the next morning
        e["published_at"] = pd.concat([pd.to_datetime(e["detected_at"], utc=True), next_morning], axis=1).max(axis=1)
        e = e.rename(columns={"title": "headline", "source_url": "url"})
        e["source"] = "sofo-event:" + e["event_type"]
        e["kind"] = "event"
        frames.append(e)
    if not frames:
        return pd.DataFrame(columns=["published_at", "ticker", "headline", "body", "source", "url", "kind"])
    df = pd.concat(frames, ignore_index=True)
    _map_cik(df, sec_tickers, "ticker", "cik")
    df = df.dropna(subset=["ticker", "published_at", "headline"])
    return _dedupe(df)


def relationships_table(rel: pd.DataFrame, sec_tickers: pd.DataFrame | None = None) -> pd.DataFrame:
    if not len(rel):
        return rel
    rel = rel[rel["valid_to"].isna()].copy()
    _map_cik(rel, sec_tickers, "ticker", "cik")
    _map_cik(rel, sec_tickers, "other_ticker", "other_cik")
    rel = rel.dropna(subset=["ticker", "other_ticker"])
    rel = rel[rel["ticker"] != rel["other_ticker"]].drop_duplicates(["ticker", "other_ticker", "rel_type"])
    return rel[["ticker", "other_ticker", "rel_type", "confidence", "first_seen"]]


def run(since: str = "2018-01-01", tables=TABLES, transport=None) -> dict:
    with client(transport=transport) as c:
        added = {t: sync(c, t, since) for t in tables}
    sec = DATA_DIR / "sec_tickers.parquet"
    sect = pd.read_parquet(sec) if sec.exists() else None
    news = news_table(load("news"), load("events"), sect)
    publish(news, relationships_table(load("relationships"), sect), via="Sofo World")
    obs = load("sec")
    if len(obs):
        from . import sofo_finance
        out = sofo_finance.build(obs, None)
        for k, v in sofo_finance.write(out).items():
            print(f"  {k}: {v}")
    (STORE / "last_sync.json").write_text(json.dumps(dict(at=pd.Timestamp.now("UTC").isoformat(), added=added)))
    return added


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2018-01-01", help="first run only: how far back to pull")
    ap.add_argument("--only", choices=TABLES, action="append")
    a = ap.parse_args()
    added = run(a.since, tuple(a.only) if a.only else TABLES)
    print("new rows from Sofo World:", added)
