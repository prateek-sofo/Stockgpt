"""Daily price history for every listed stock.

Providers (pick one; each needs its own API key, stored as an environment network secret or env var):

* polygon  POLYGON_API_KEY. Grouped daily bars: ONE request per trading day returns every US
           stock (split-adjusted OHLCV), so 10 years ~ 2,520 requests. Same vendor also sells
           historical options (needed for a real options backtest).
* tiingo   TIINGO_API_KEY. One request per ticker, full history, adjusted.
* csv      any vendor export / S3 dump: data/raw/prices/*.csv|parquet with
           date,ticker,open,high,low,close,volume

    python -m stockgpt.sources.prices --provider polygon --start 2016-01-01
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import pandas as pd

from ..config import DATA_DIR

OUT = DATA_DIR / "prices.parquet"
UA = {"User-Agent": "StockGPT research"}


def _session():
    import httpx
    return httpx.Client(timeout=60, headers=UA)


def polygon(start: str, end: str | None = None, tickers: set[str] | None = None,
            min_interval_s: float = 0.0, cache_dir: Path | None = None) -> pd.DataFrame:
    """Grouped daily bars. min_interval_s=12.5 for the free tier (5 requests/minute)."""
    key = os.environ["POLYGON_API_KEY"]
    cache = cache_dir or DATA_DIR / "prices" / "polygon_daily"
    cache.mkdir(parents=True, exist_ok=True)
    days = pd.bdate_range(start, end or pd.Timestamp.today().normalize())
    frames = []
    with _session() as s:
        for d in days:
            f = cache / f"{d:%Y-%m-%d}.parquet"
            if f.exists():
                frames.append(pd.read_parquet(f))
                continue
            for attempt in range(4):
                r = s.get(f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{d:%Y-%m-%d}",
                          params={"adjusted": "true", "include_otc": "false", "apiKey": key})
                if r.status_code == 429:
                    time.sleep(15 * (attempt + 1))
                    continue
                r.raise_for_status()
                break
            res = r.json().get("results") or []          # empty on market holidays
            df = pd.DataFrame(res).rename(columns={"T": "ticker", "o": "open", "h": "high", "l": "low",
                                                   "c": "close", "v": "volume", "vw": "vwap", "n": "trades"})
            if len(df):
                df["date"] = d
                df = df[["date", "ticker", "open", "high", "low", "close", "volume", "vwap", "trades"]]
            df.to_parquet(f, index=False)
            frames.append(df)
            time.sleep(min_interval_s)
    out = pd.concat([f for f in frames if len(f)], ignore_index=True)
    if tickers:
        out = out[out["ticker"].isin(tickers)]
    return out


def tiingo(tickers: list[str], start: str, min_interval_s: float = 0.1) -> pd.DataFrame:
    key = os.environ["TIINGO_API_KEY"]
    frames = []
    with _session() as s:
        for tk in tickers:
            r = s.get(f"https://api.tiingo.com/tiingo/daily/{tk.replace('.', '-')}/prices",
                      params={"startDate": start, "token": key, "format": "json"})
            if r.status_code == 404:
                continue
            r.raise_for_status()
            df = pd.DataFrame(r.json())
            if len(df):
                frames.append(pd.DataFrame({
                    "date": pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize(), "ticker": tk,
                    "open": df["adjOpen"], "high": df["adjHigh"], "low": df["adjLow"],
                    "close": df["adjClose"], "volume": df["adjVolume"]}))
            time.sleep(min_interval_s)
    return pd.concat(frames, ignore_index=True)


def from_files(folder: Path | None = None) -> pd.DataFrame:
    folder = folder or DATA_DIR / "prices"
    files = sorted(list(folder.glob("*.parquet")) + list(folder.glob("*.csv")))
    frames = [pd.read_parquet(f) if f.suffix == ".parquet" else pd.read_csv(f) for f in files]
    df = pd.concat(frames, ignore_index=True)
    df.columns = [c.lower() for c in df.columns]
    return df[["date", "ticker", "open", "high", "low", "close", "volume"]]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["polygon", "tiingo", "csv"], required=True)
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--free-tier", action="store_true", help="polygon: 5 requests/minute")
    a = ap.parse_args()
    sec = pd.read_parquet(DATA_DIR / "sec_tickers.parquet")
    universe = set(sec["ticker"])
    if a.provider == "polygon":
        df = polygon(a.start, tickers=universe, min_interval_s=12.5 if a.free_tier else 0.0)
    elif a.provider == "tiingo":
        df = tiingo(sorted(universe), a.start)
    else:
        df = from_files()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df.to_parquet(OUT, index=False)
    print(f"prices: {len(df):,} rows, {df['ticker'].nunique():,} tickers, {df['date'].min():%Y-%m-%d} -> {df['date'].max():%Y-%m-%d}")
