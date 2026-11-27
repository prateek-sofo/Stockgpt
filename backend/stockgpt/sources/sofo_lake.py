"""Sofo World's S3 lake, read through Athena (the route since Sofo World moved to AWS).

Everything the hub and the AWS crawlers collect lands in the bucket `sofo-lake-<account>` and is queryable as the
Athena database `lake`. This module is the one place StockGPT reads it. It never writes.

Cost: Athena bills by data scanned ($5 per TB, 10 MB minimum, about $0.00005). The `cc` workgroup stops any query
past 10 GB. Every read here filters on a date so a two-minute poll scans only the newest files; the bytes each
query scanned are returned and logged so the spend is visible.

Needs AWS credentials with read access to the lake (environment variables AWS_ACCESS_KEY_ID and
AWS_SECRET_ACCESS_KEY, or an instance role on the AWS box).

    python -m stockgpt.sources.sofo_lake tables            # what the lake holds, with columns
    python -m stockgpt.sources.sofo_lake query "SELECT ..."  # one read-only statement
    python -m stockgpt.sources.sofo_lake news --since 2026-01-01  # news table for the model
"""
from __future__ import annotations

import argparse
import os
import re
import time

import pandas as pd

REGION = os.getenv("SOFO_LAKE_REGION", "us-east-1")
WORKGROUP = os.getenv("SOFO_LAKE_WORKGROUP", "cc")
DATABASE = os.getenv("SOFO_LAKE_DATABASE", "lake")
USD_PER_TB = 5.0
_READ = re.compile(r"^\s*(select|with|show|describe)\b", re.I)


def _athena():
    import boto3  # optional dependency

    return boto3.client("athena", region_name=REGION)


def query(sql: str, timeout: int = 120) -> tuple[pd.DataFrame, dict]:
    """Run one read-only statement. Returns the rows and what it cost: {'bytes': scanned, 'usd': estimate}."""
    if not _READ.match(sql) or ";" in sql.strip().rstrip(";"):
        raise ValueError("read-only: one SELECT, WITH, SHOW or DESCRIBE statement")
    a = _athena()
    qid = a.start_query_execution(QueryString=sql, QueryExecutionContext={"Database": DATABASE}, WorkGroup=WORKGROUP)["QueryExecutionId"]
    t0 = time.time()
    while True:
        ex = a.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        state = ex["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        if time.time() - t0 > timeout:
            a.stop_query_execution(QueryExecutionId=qid)
            raise TimeoutError(f"lake query passed {timeout}s and was stopped")
        time.sleep(0.7)
    if state != "SUCCEEDED":
        raise RuntimeError(f"lake query {state}: {ex['Status'].get('StateChangeReason', '')}")
    scanned = int(ex.get("Statistics", {}).get("DataScannedInBytes", 0))
    rows, cols = [], None
    for page in a.get_paginator("get_query_results").paginate(QueryExecutionId=qid):
        if cols is None:
            cols = [c["Name"] for c in page["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]]
        rows += [[c.get("VarCharValue") for c in r["Data"]] for r in page["ResultSet"]["Rows"]]
    if rows and cols and rows[0] == cols:              # SELECT results repeat the header as the first row
        rows = rows[1:]
    return pd.DataFrame(rows, columns=cols), dict(bytes=scanned, usd=round(max(scanned, 10e6) / 1e12 * USD_PER_TB, 6))


def tables() -> dict[str, list[tuple[str, str]]]:
    """Every table in the lake with its columns, from the Glue catalog (no data is scanned, no cost)."""
    import boto3

    g = boto3.client("glue", region_name=REGION)
    out = {}
    for page in g.get_paginator("get_tables").paginate(DatabaseName=DATABASE):
        for t in page["TableList"]:
            sd = t.get("StorageDescriptor", {})
            out[t["Name"]] = [(c["Name"], c["Type"]) for c in sd.get("Columns", [])] + \
                [(c["Name"], c["Type"] + " (partition)") for c in t.get("PartitionKeys", [])]
    return out


NEWS_SQL = """
WITH sec AS (
  SELECT domain, CAST(cik AS varchar) AS cik FROM bigco_sec_company WHERE "$path" LIKE '%sec_company%'
)
SELECT m.article_id, sec.cik, a.title AS headline, m.snippet AS body, a.url, a.publisher_domain AS source,
       a.published_at, a.fetched_at, m.salience, m.role
FROM news_mentions m
JOIN sec ON sec.domain = m.entity_domain
JOIN news_articles a ON a.article_id = m.article_id AND a.month = m.month
WHERE m.month >= '{month}' AND a.published_at >= TIMESTAMP '{since}'
  AND (m.prominent OR m.salience >= {min_salience})
  AND a.title IS NOT NULL
"""


def news(since: str = "2026-01-01", sec_tickers: pd.DataFrame | None = None, min_salience: float = 0.5) -> pd.DataFrame:
    """News per (article, ticker) from the lake, in the `news` table shape the model reads.

    Companies are matched by website domain to an SEC CIK (lake table bigco_sec_company), then CIK to
    ticker with the SEC listing. published_at is pushed forward to when we first fetched the article
    (capped at +6 h) so a late-found story is never visible to the backtest earlier than we had it.
    """
    from .sofo_world import _dedupe

    sql = NEWS_SQL.format(month=since[:7], since=f"{since[:10]} 00:00:00", min_salience=min_salience)
    df, cost = query(sql, timeout=600)
    print(f"lake news: {len(df):,} mention rows, scanned {cost['bytes'] / 1e6:.0f} MB (about ${cost['usd']})")
    if df.empty:
        return df
    for c in ("published_at", "fetched_at"):
        df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")
    late = df["fetched_at"].clip(upper=df["published_at"] + pd.Timedelta(hours=6))
    df["published_at"] = pd.concat([df["published_at"], late], axis=1).max(axis=1)
    if sec_tickers is None:
        raise SystemExit("needs the SEC listing (sec_tickers) to turn CIKs into tickers")
    t = sec_tickers.assign(cik=sec_tickers["cik"].astype(str).str.lstrip("0")).drop_duplicates("cik")
    df["ticker"] = df["cik"].astype(str).str.lstrip("0").map(t.set_index("cik")["ticker"])
    df["kind"] = "article"
    df = df.dropna(subset=["ticker", "published_at", "headline"])
    return _dedupe(df)


def sec_listing() -> pd.DataFrame:
    """SEC's company_tickers.json as (cik, ticker, name); cached in DATA_DIR/sec_tickers.parquet."""
    import json
    import urllib.request

    from ..config import DATA_DIR

    path = DATA_DIR / "sec_tickers.parquet"
    if path.exists():
        return pd.read_parquet(path)
    ua = f"StockGPT research {os.environ.get('SEC_CONTACT', 'data@sofo.ai')}"
    req = urllib.request.Request("https://www.sec.gov/files/company_tickers.json", headers={"User-Agent": ua})
    rows = json.load(urllib.request.urlopen(req, timeout=30)).values()
    out = pd.DataFrame([(str(r["cik_str"]), r["ticker"].upper(), r["title"]) for r in rows], columns=["cik", "ticker", "name"])
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path, index=False)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["tables", "query", "news"])
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("sql", nargs="?")
    a = ap.parse_args()
    if a.cmd == "tables":
        for name, cols in sorted(tables().items()):
            print(f"{name}\n  " + ", ".join(f"{c} {t}" for c, t in cols))
    elif a.cmd == "news":
        from .sofo_world import publish
        publish(news(a.since, sec_listing()), via="Sofo World lake")
    else:
        df, cost = query(a.sql)
        print(df.to_string(max_rows=50, max_colwidth=80))
        print(f"\n{len(df)} rows · scanned {cost['bytes'] / 1e6:.1f} MB · about ${cost['usd']}")
