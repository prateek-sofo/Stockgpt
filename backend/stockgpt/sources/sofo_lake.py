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


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["tables", "query"])
    ap.add_argument("sql", nargs="?")
    a = ap.parse_args()
    if a.cmd == "tables":
        for name, cols in sorted(tables().items()):
            print(f"{name}\n  " + ", ".join(f"{c} {t}" for c, t in cols))
    else:
        df, cost = query(a.sql)
        print(df.to_string(max_rows=50, max_colwidth=80))
        print(f"\n{len(df)} rows · scanned {cost['bytes'] / 1e6:.1f} MB · about ${cost['usd']}")
