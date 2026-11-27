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
    sj, raw = DATA_DIR / "sources.json", DATA_DIR / "sec" / "company_tickers_exchange.json"
    from_sofo = sj.exists() and json.loads(sj.read_text()).get("financials") == "Sofo World"
    if LISTED.exists() and (from_sofo or not raw.exists()):
        return pd.read_parquet(LISTED)             # the listing Sofo World supplied
    d = json.loads(raw.read_text())
    t = pd.DataFrame(d["data"], columns=d["fields"])
    t["ticker"] = t["ticker"].str.upper().str.replace("-", ".", regex=False)
    t["exchange"] = t["exchange"].fillna("Other")
    return t.drop_duplicates("ticker")[["ticker", "name", "cik", "exchange"]]


def build(days: int = 400, fresh: bool = True, refresh: bool = False) -> pd.DataFrame:
    t = listed()
    t.to_parquet(LISTED, index=False)
    start = (pd.Timestamp.now("UTC").normalize() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    cache = DATA_DIR / "prices" / "yahoo_all"
    if cache.exists() and (refresh or (fresh and not any(cache.glob(f"*_{start}.parquet")))):
        shutil.rmtree(cache)                       # yesterday's batches, or a same-day rerun after the close: refetch
    px = yahoo(t["ticker"].tolist(), start, cache_dir=cache, threads=2, pause_s=2.0)   # two connections at a time
    px["date"] = pd.to_datetime(px["date"]).dt.normalize()
    px = px.drop_duplicates(["date", "ticker"]).sort_values(["ticker", "date"])
    px.to_parquet(OUT, index=False)
    return px


def _store():
    """The long price history of every listed ticker. `assemble` reads it and writes the model's own
    filtered copy (prices.parquet); it is never filtered or overwritten itself."""
    raw = DATA_DIR / "prices_raw.parquet"
    return raw if raw.exists() else DATA_DIR / "prices.parquet"


def merge_into_model_prices() -> None:
    """Bring prices.parquet (the model's long history) up to date from the all-ticker table. Rows are
    added or replaced one (date, ticker) at a time: a ticker missing from today's download keeps the
    rows it already had, so a throttled download can never punch holes in the history."""
    old = pd.read_parquet(_store())
    old["date"] = pd.to_datetime(old["date"]).dt.normalize()
    new = pd.read_parquet(OUT)
    cols = [c for c in old.columns if c in new.columns]           # extra columns (premarket) are re-attached by assemble
    cut = old["date"].max() - pd.Timedelta(days=30)
    new = new[(new["date"] > cut) & new["ticker"].isin(set(old["ticker"]))].dropna(subset=["open", "close"])
    df = pd.concat([old[cols], new[cols]], ignore_index=True)
    df = df.drop_duplicates(["date", "ticker"], keep="last").sort_values(["date", "ticker"])
    df.to_parquet(_store(), index=False)
    print(f"model prices now through {df['date'].max():%Y-%m-%d}; {coverage(df)['note']}")


def coverage(prices: pd.DataFrame, sessions: int = 15, tolerance: float = 0.02) -> dict:
    """Are the recent sessions complete? Compares each of the last sessions' stock count with the
    typical count over the two months before. A short day means a download was cut off."""
    n = prices.dropna(subset=["close"]).groupby("date")["ticker"].nunique().sort_index()
    recent, base = n.tail(sessions), n.iloc[-(sessions + 40):-sessions]
    typical = float(base.median()) if len(base) else float(recent.max())
    short = recent[recent < typical * (1 - tolerance)]
    ok = short.empty
    note = (f"complete: about {typical:,.0f} stocks on each of the last {len(recent)} sessions" if ok else
            f"INCOMPLETE: {len(short)} of the last {len(recent)} sessions are missing stocks "
            f"(worst {pd.Timestamp(short.idxmin()):%b %d}: {int(short.min()):,} of about {typical:,.0f})")
    return dict(ok=ok, note=note, short_days=[str(pd.Timestamp(d).date()) for d in short.index], typical=typical)


def repair(days: int = 45) -> None:
    """Refetch the recent history of the model's own tickers and fill any holes."""
    old = pd.read_parquet(_store())
    old["date"] = pd.to_datetime(old["date"]).dt.normalize()
    start = (old["date"].max() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    tick = sorted(set(old["ticker"]))
    cache = DATA_DIR / "prices" / "yahoo_repair"
    if cache.exists() and not any(cache.glob(f"*_{start}.parquet")):
        shutil.rmtree(cache)                       # batches from another day; today's are reused
    got = yahoo(tick, start, cache_dir=cache, threads=2, pause_s=2.0)
    missing = sorted(set(tick) - set(got["ticker"]))
    if missing and len(missing) < 0.2 * len(tick):  # second, slower pass for whatever was throttled (not for a universe of delisted names)
        print(f"  retrying {len(missing)} tickers", flush=True)
        import time
        time.sleep(60)
        more = yahoo(missing, start, batch=50, cache_dir=cache / "retry", threads=False, pause_s=3.0)
        if len(more):
            got = pd.concat([got, more], ignore_index=True)
    got["date"] = pd.to_datetime(got["date"]).dt.normalize()
    cols = [c for c in old.columns if c in got.columns]
    df = pd.concat([old[cols], got.dropna(subset=["open", "close"])[cols]], ignore_index=True)
    df = df.drop_duplicates(["date", "ticker"], keep="last").sort_values(["date", "ticker"])
    df.to_parquet(_store(), index=False)
    print(f"repair: {got['ticker'].nunique():,} of {len(tick):,} tickers refetched; {coverage(df)['note']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--merge", action="store_true", help="also update the model's prices.parquet")
    ap.add_argument("--refresh", action="store_true", help="ignore batches downloaded earlier today (use after the close)")
    ap.add_argument("--repair", action="store_true", help="refetch the model tickers' recent history and fill holes")
    a = ap.parse_args()
    if a.repair:
        repair()
        raise SystemExit
    px = build(a.days, refresh=a.refresh)
    print(f"all tickers: {px['ticker'].nunique():,} with prices, through {px['date'].max():%Y-%m-%d}")
    if a.merge:
        merge_into_model_prices()
