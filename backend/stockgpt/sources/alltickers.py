"""Every publicly traded ticker the SEC lists (about 10,400: NYSE, Nasdaq, CBOE, OTC), with one year of
daily prices from Yahoo. The model only ranks liquid stocks; this table is what lets the app show
price, filings and news for all of them.

    python -m stockgpt.sources.alltickers            # data/raw/prices_all.parquet + listed.parquet
"""
from __future__ import annotations

import argparse
import json
import shutil

import pandas as pd

from ..config import DATA_DIR
from .prices import yahoo

OUT = DATA_DIR / "prices_all.parquet"
LISTED = DATA_DIR / "listed.parquet"


def listed() -> pd.DataFrame:
    d = json.loads((DATA_DIR / "sec" / "company_tickers_exchange.json").read_text())
    t = pd.DataFrame(d["data"], columns=d["fields"])
    t["ticker"] = t["ticker"].str.upper().str.replace("-", ".", regex=False)
    t["exchange"] = t["exchange"].fillna("Other")
    return t.drop_duplicates("ticker")[["ticker", "name", "cik", "exchange"]]


def build(days: int = 400, fresh: bool = True) -> pd.DataFrame:
    t = listed()
    t.to_parquet(LISTED, index=False)
    start = (pd.Timestamp.now("UTC").normalize() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    cache = DATA_DIR / "prices" / "yahoo_all"
    if fresh and cache.exists() and not any(cache.glob(f"*_{start}.parquet")):
        shutil.rmtree(cache)                       # yesterday's batches: refetch
    px = yahoo(t["ticker"].tolist(), start, cache_dir=cache, threads=2, pause_s=2.0)   # two connections at a time
    px["date"] = pd.to_datetime(px["date"]).dt.normalize()
    px = px.drop_duplicates(["date", "ticker"]).sort_values(["ticker", "date"])
    px.to_parquet(OUT, index=False)
    return px


def merge_into_model_prices() -> None:
    """Bring prices.parquet (the model's long history) up to date from the all-ticker table."""
    old = pd.read_parquet(DATA_DIR / "prices.parquet")
    old["date"] = pd.to_datetime(old["date"]).dt.normalize()
    new = pd.read_parquet(OUT)
    cut = old["date"].max() - pd.Timedelta(days=7)
    new = new[(new["date"] > cut) & new["ticker"].isin(set(old["ticker"]))]
    df = pd.concat([old[old["date"] <= cut], new[old.columns]], ignore_index=True)
    df = df.drop_duplicates(["date", "ticker"], keep="last").sort_values(["date", "ticker"])
    df.to_parquet(DATA_DIR / "prices.parquet", index=False)
    print(f"model prices now through {df['date'].max():%Y-%m-%d}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--merge", action="store_true", help="also update the model's prices.parquet")
    a = ap.parse_args()
    px = build(a.days)
    print(f"all tickers: {px['ticker'].nunique():,} with prices, through {px['date'].max():%Y-%m-%d}")
    if a.merge:
        merge_into_model_prices()
