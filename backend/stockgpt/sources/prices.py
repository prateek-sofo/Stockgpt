"""Daily price history for every listed stock.

Providers:

* yahoo    default, open source, no key: yfinance over Yahoo Finance public daily bars.

Keyed alternatives (stored as an environment network secret or env var):

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


def yahoo(tickers: list[str], start: str, batch: int = 100, pause_s: float = 2.0,
          cache_dir: Path | None = None, threads: int | bool = False) -> pd.DataFrame:
    """Open-source route: yfinance (Apache-2.0) over Yahoo Finance's public daily bars.
    Split- and dividend-adjusted OHLCV. Batched and paced; each batch is cached so a rerun resumes."""
    import yfinance as yf
    cache = cache_dir or DATA_DIR / "prices" / "yahoo"
    cache.mkdir(parents=True, exist_ok=True)
    syms = sorted(tickers)
    frames = []
    for i in range(0, len(syms), batch):
        chunk = syms[i:i + batch]
        f = cache / f"batch_{i // batch:04d}_{start}.parquet"
        if f.exists():
            frames.append(pd.read_parquet(f))
            continue
        ymap = {t.replace(".", "-"): t for t in chunk}            # BRK.B -> BRK-B on Yahoo
        raw = yf.download(list(ymap), start=start, auto_adjust=True, progress=False, threads=threads,
                          group_by="column")
        if raw is None or raw.empty:
            pd.DataFrame().to_parquet(f)
            continue
        df = raw.stack(level=1, future_stack=True).reset_index()
        df.columns = [str(c).lower() for c in df.columns]
        df = df.rename(columns={"ticker": "ticker", "level_1": "ticker"})
        df["ticker"] = df["ticker"].map(lambda y: ymap.get(y, y))
        df = df.dropna(subset=["close"])[["date", "ticker", "open", "high", "low", "close", "volume"]]
        df.to_parquet(f, index=False)
        frames.append(df)
        print(f"  batch {i // batch + 1}/{-(-len(syms) // batch)}: {df['ticker'].nunique()} tickers", flush=True)
        time.sleep(pause_s)
    from ..sessions import closed_only
    frames = [f for f in frames if len(f)]
    if not frames:                                 # nothing came back (delisted names, or a throttled run)
        return pd.DataFrame(columns=["date", "ticker", "open", "high", "low", "close", "volume"])
    return closed_only(pd.concat(frames, ignore_index=True))   # never an unfinished session


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
    ap.add_argument("--provider", choices=["yahoo", "polygon", "tiingo", "csv"], default="yahoo")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--free-tier", action="store_true", help="polygon: 5 requests/minute")
    ap.add_argument("--update", action="store_true",
                    help="append to the existing prices.parquet: refetch the last 7 days, keep older rows")
    a = ap.parse_args()
    old = None
    if a.update and OUT.exists():
        old = pd.read_parquet(OUT)
        a.start = (pd.to_datetime(old["date"]).max() - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    sec = pd.read_parquet(DATA_DIR / "sec_tickers.parquet")
    universe = set(sec["ticker"])
    if a.provider == "yahoo":
        df = yahoo(sorted(universe), a.start)
    elif a.provider == "polygon":
        df = polygon(a.start, tickers=universe, min_interval_s=12.5 if a.free_tier else 0.0)
    elif a.provider == "tiingo":
        df = tiingo(sorted(universe), a.start)
    else:
        df = from_files()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    if old is not None:
        old["date"] = pd.to_datetime(old["date"]).dt.normalize()
        df = pd.concat([old[old["date"] < pd.Timestamp(a.start)], df], ignore_index=True)
        df = df.drop_duplicates(["date", "ticker"], keep="last").sort_values(["date", "ticker"])
    df.to_parquet(OUT, index=False)
    print(f"prices: {len(df):,} rows, {df['ticker'].nunique():,} tickers, {df['date'].min():%Y-%m-%d} -> {df['date'].max():%Y-%m-%d}")
