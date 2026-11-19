"""Recent headlines per ticker and for the wider market, from Yahoo Finance's public search feed.

Only the headline, publisher, time and link are stored (no article text). A headline is kept for a
ticker only when Yahoo itself tags that ticker on the article.

    python -m stockgpt.sources.headlines             # data/raw/headlines.parquet (appends)
    python -m stockgpt.sources.headlines --top 600   # most liquid names only
"""
from __future__ import annotations

import argparse
import json
import time

import httpx
import pandas as pd

from ..config import DATA_DIR

OUT = DATA_DIR / "headlines.parquet"
URL = "https://query1.finance.yahoo.com/v1/finance/search"
UA = {"User-Agent": "StockGPT research"}
# Yahoo's search returns news by symbol, not by phrase: market-wide stories are the ones tagged to an
# index, a Treasury yield or a commodity. The tracker sorts them into topics by keyword.
MACRO = ["^GSPC", "^DJI", "^IXIC", "^TNX", "^VIX", "CL=F", "GC=F", "DX-Y.NYB", "SPY", "TLT"]

def _search(client: httpx.Client, q: str, n: int = 10) -> list[dict]:
    for attempt in range(3):
        try:
            r = client.get(URL, params=dict(q=q, newsCount=n, quotesCount=0))
        except httpx.TransportError:               # dropped connection: wait, then try again
            time.sleep(15 * (attempt + 1))
            continue
        if r.status_code == 200:
            return r.json().get("news", [])
        if r.status_code in (403, 429):
            time.sleep(20 * (attempt + 1))         # back off; stop after three tries
            continue
        return []
    raise RateLimited("Yahoo search refused or dropped three requests in a row")


class RateLimited(RuntimeError):
    pass


def _rows(pairs) -> pd.DataFrame:
    return pd.DataFrame([dict(ticker=t, published_at=pd.Timestamp(a["providerPublishTime"], unit="s", tz="UTC"),
                              headline=a["title"], source=a.get("publisher", ""), url=a.get("link", ""), id=a["uuid"])
                         for t, a in pairs], columns=["ticker", "published_at", "headline", "source", "url", "id"])


def _save(pairs) -> pd.DataFrame:
    df = _rows(pairs)
    if OUT.exists():
        df = pd.concat([pd.read_parquet(OUT), df], ignore_index=True)
    df["published_at"] = pd.to_datetime(df["published_at"], utc=True)
    df = df.drop_duplicates(["ticker", "id"]).sort_values("published_at").reset_index(drop=True)
    df.to_parquet(OUT, index=False)
    return df


def build(tickers: list[str], pause: float = 0.3) -> pd.DataFrame:
    """Market topics first, then tickers in the order given (most traded first). Saved every 200
    tickers; a ticker checked in the last 12 hours is skipped, so an interrupted run resumes. If Yahoo
    throttles us the run stops and keeps what it has."""
    done_f = DATA_DIR / "headlines_checked.json"
    checked: dict[str, float] = json.loads(done_f.read_text()) if done_f.exists() else {}
    now = time.time()
    todo = [t for t in tickers if now - checked.get(t, 0) > 12 * 3600]
    pairs = []
    try:
        with httpx.Client(timeout=30, headers=UA) as client:
            for sym in MACRO:
                pairs += [("_macro", a) for a in _search(client, sym, 12) if sym in a.get("relatedTickers", [])]
                time.sleep(max(pause, 1.5))
            for i, t in enumerate(todo):
                y = t.replace(".", "-")
                pairs += [(t, a) for a in _search(client, y) if y in a.get("relatedTickers", [])]
                checked[t] = now
                time.sleep(pause)
                if i % 200 == 199:
                    _save(pairs)
                    pairs = []
                    done_f.write_text(json.dumps(checked))
                    print(f"  headlines: {i + 1}/{len(todo)}", flush=True)
    except RateLimited as e:
        print(f"  stopped early: {e}", flush=True)
    df = _save(pairs)
    done_f.write_text(json.dumps(checked))
    return df


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=0, help="only the N most traded tickers (0 = every model ticker)")
    ap.add_argument("--macro-only", action="store_true", help="market-wide stories only")
    a = ap.parse_args()
    if a.macro_only:
        df = build([])
        print(f"headlines: {int((df['ticker'] == '_macro').sum()):,} market-wide stories")
        raise SystemExit
    src = DATA_DIR / "prices.parquet"            # the model's stocks; on a tracker-only machine, every priced ticker
    px = pd.read_parquet(src if src.exists() else DATA_DIR / "prices_all.parquet", columns=["date", "ticker", "close", "volume"])
    px["date"] = pd.to_datetime(px["date"])
    px = px[px["date"] >= px["date"].max() - pd.Timedelta(days=30)]
    dv = (px["close"] * px["volume"]).groupby(px["ticker"]).mean().sort_values(ascending=False)
    tickers = list(dv.index[:a.top] if a.top else dv.index)
    df = build(tickers)
    print(f"headlines: {len(df):,} rows, {df['ticker'].nunique():,} tickers/topics, latest {df['published_at'].max()}")
