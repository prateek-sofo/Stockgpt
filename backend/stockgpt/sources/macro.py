"""The macro and political world, as daily series (all open, public sources).

* GDELT DOC 2.0 timelines (https://api.gdeltproject.org): for each theme, the share of world news
  coverage and the average tone of that coverage per day, since 2017. Themes cover the economy
  (inflation, rates, central banks, jobs, trade disputes, oil, debt, banking) and politics (elections,
  legislation, sanctions, armed conflict, terrorism, protests, regulation, tariffs).
* FRED CSV downloads (no key): 10y and 2y Treasury yields, the 10y-2y curve, high-yield credit spread,
  10y breakeven inflation, effective Fed funds rate.
* Yahoo Finance (yfinance): VIX, dollar index, WTI oil, gold, S&P 500, and the 11 SPDR sector ETFs.

Point-in-time: every value is lagged so the row for trading day t holds only what was known at 09:00 ET
on t (GDELT day t-1 in UTC, FRED/market closes of t-1).

    python -m stockgpt.sources.macro --start 2017-01-01      # writes data/raw/macro.parquet
"""
from __future__ import annotations

import argparse
import hashlib
import io
import time
from pathlib import Path

import pandas as pd

from ..config import DATA_DIR

UA = {"User-Agent": "StockGPT research data@sofo.ai"}
OUT = DATA_DIR / "macro.parquet"
CACHE = DATA_DIR / "macro_cache"

GDELT_THEMES = {
    # economy
    "inflation": "theme:ECON_INFLATION",
    "rates": "theme:ECON_INTEREST_RATES",
    "centralbank": "theme:ECON_CENTRALBANK",
    "jobs": "theme:UNEMPLOYMENT",
    "trade_dispute": "theme:ECON_TRADE_DISPUTE",
    "oil": "theme:ECON_OILPRICE",
    "debt": "theme:ECON_DEBT",
    "banking": "theme:ECON_BANKRUPTCY",
    # politics and the world
    "election": "theme:ELECTION",
    "legislation": "theme:LEGISLATION",
    "sanctions": "theme:SANCTIONS",
    "conflict": "theme:ARMEDCONFLICT",
    "terror": "theme:TERROR",
    "protest": "theme:PROTEST",
    "regulation": "theme:GENERAL_GOVERNMENT",
    "tariffs": '"tariffs"',
}
FRED = {"epu": "USEPUINDXD",          # Economic Policy Uncertainty: daily, newspaper-based (Baker-Bloom-Davis)
        "emv_epu": "WLEMUINDXD",      # equity-market-related economic uncertainty, daily
        "dgs10": "DGS10", "dgs2": "DGS2", "curve_10_2": "T10Y2Y", "hy_spread": "BAMLH0A0HYM2",
        "breakeven10": "T10YIE", "fedfunds": "DFF"}
MARKET = {"vix": "^VIX", "dxy": "DX-Y.NYB", "oil": "CL=F", "gold": "GC=F", "spx": "^GSPC",
          "xlk": "XLK", "xlf": "XLF", "xlv": "XLV", "xle": "XLE", "xli": "XLI", "xly": "XLY",
          "xlp": "XLP", "xlb": "XLB", "xlu": "XLU", "xlre": "XLRE", "xlc": "XLC"}
# sector name (SEC SIC mapping in sources/sec.py) -> sector ETF
SECTOR_ETF = {"Technology": "xlk", "Financials": "xlf", "Healthcare": "xlv", "Energy": "xle",
              "Industrials": "xli", "Consumer Disc.": "xly", "Consumer Staples": "xlp", "Materials": "xlb",
              "Utilities": "xlu", "Real Estate": "xlre", "Communication": "xlc"}


def _gdelt(query: str, mode: str, start: str, end: str, pause: float = 6.0) -> pd.Series:
    """One GDELT timeline, fetched by calendar year (daily resolution), paced at GDELT's 1 per 5 s."""
    import httpx
    CACHE.mkdir(parents=True, exist_ok=True)
    parts = []
    years = range(pd.Timestamp(start).year, pd.Timestamp(end).year + 1)
    with httpx.Client(timeout=90, headers=UA) as s:
        for y in years:
            a = max(pd.Timestamp(start), pd.Timestamp(f"{y}-01-01"))
            b = min(pd.Timestamp(end), pd.Timestamp(f"{y}-12-31"))
            key = CACHE / f"gdelt_{mode}_{hashlib.sha1(query.encode()).hexdigest()[:10]}_{y}.parquet"
            if key.exists() and y < pd.Timestamp.today().year:
                parts.append(pd.read_parquet(key)["v"])
                continue
            q = f"{query} sourcelang:english"
            for attempt in range(8):
                time.sleep(pause * (1 + attempt))
                r = s.get("https://api.gdeltproject.org/api/v2/doc/doc", params=dict(
                    query=q, mode=mode, format="json", timelinesmooth=0,
                    startdatetime=a.strftime("%Y%m%d000000"), enddatetime=b.strftime("%Y%m%d235959")))
                if r.status_code == 200 and r.text.lstrip().startswith("{"):
                    break
            else:
                raise RuntimeError(f"GDELT kept refusing {query} {y}: {r.text[:120]}")
            tl = r.json().get("timeline") or []
            data = tl[0]["data"] if tl else []
            ser = pd.Series({pd.Timestamp(d["date"][:8]): float(d["value"]) for d in data}, name="v", dtype=float)
            ser.to_frame().to_parquet(key)
            parts.append(ser)
    out = pd.concat(parts) if parts else pd.Series(dtype=float)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out.resample("D").mean()


def _fred(series_id: str) -> pd.Series:
    import httpx
    r = httpx.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series_id}, headers=UA, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), na_values=["."])
    df.columns = ["date", "v"]
    return df.set_index(pd.to_datetime(df["date"]))["v"].astype(float)


def _market(start: str) -> pd.DataFrame:
    import yfinance as yf
    raw = yf.download(list(MARKET.values()), start=start, auto_adjust=True, progress=False, threads=False)
    close = raw["Close"].rename(columns={v: k for k, v in MARKET.items()})
    return close


def _gpr() -> pd.DataFrame | None:
    """Caldara-Iacoviello daily Geopolitical Risk index (newspaper-based), if reachable."""
    import httpx
    url = "https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls"
    try:
        r = httpx.get(url, headers=UA, timeout=120, follow_redirects=True)
        r.raise_for_status()
        df = pd.read_excel(io.BytesIO(r.content))
    except Exception as e:
        print(f"  GPR skipped: {str(e)[:80]}")
        return None
    df.columns = [str(c).lower() for c in df.columns]
    date_col = "date" if "date" in df else df.columns[0]
    df.index = pd.to_datetime(df[date_col])
    out = pd.DataFrame(index=df.index)
    for src, dst in (("gprd", "gpr"), ("gprd_act", "gpr_acts"), ("gprd_threat", "gpr_threats")):
        if src in df:
            out[f"politics_{dst}"] = pd.to_numeric(df[src], errors="coerce")
    return out if len(out.columns) else None


def build(start: str = "2017-01-01", use_gdelt: bool = True) -> pd.DataFrame:
    end = pd.Timestamp.today().normalize()
    cols = {}
    if use_gdelt:
        for name, q in GDELT_THEMES.items():
            try:
                cols[f"news_{name}_vol"] = _gdelt(q, "timelinevol", start, str(end.date()))
                cols[f"news_{name}_tone"] = _gdelt(q, "timelinetone", start, str(end.date()))
                print(f"  gdelt {name}", flush=True)
            except Exception as e:                    # GDELT throttles hard: skip, never hammer it
                print(f"  gdelt {name} skipped: {str(e)[:80]}", flush=True)
                break
    gpr = _gpr()
    if gpr is not None:
        for c in gpr.columns:
            cols[c] = gpr[c]
    for name, sid in FRED.items():
        cols[f"fred_{name}"] = _fred(sid)
    mk = _market(start)
    for c in mk.columns:
        cols[f"mkt_{c}"] = mk[c]
    raw = pd.DataFrame(cols).sort_index()
    raw = raw[raw.index >= start]
    raw.to_parquet(DATA_DIR / "macro_raw.parquet")
    return raw


def daily_features(raw: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Macro/political features for each trading date, using only data up to the previous day.

    News: tone level and 5-day change, and coverage volume as a z-score vs its trailing 60 days
    ("is this theme suddenly in the news?"). Rates/credit: 1-day and 5-day changes. Markets: prior-day
    returns and VIX level/change.
    """
    cal = raw.copy()
    cal.index = pd.to_datetime(cal.index)
    full = cal.resample("D").last()
    news_cols = [c for c in full if c.startswith("news_")]
    full[news_cols] = full[news_cols].fillna(0.0)
    full = full.ffill()
    f = pd.DataFrame(index=full.index)
    for c in news_cols:
        if c.endswith("_vol"):
            base = full[c].rolling(60, min_periods=20)
            f[c.replace("_vol", "_attn")] = (full[c] - base.mean()) / (base.std() + 1e-9)
        else:
            f[c] = full[c].rolling(2, min_periods=1).mean()           # yesterday + day before
            f[c + "_chg5"] = full[c].rolling(2).mean() - full[c].rolling(2).mean().shift(5)
    for c in [c for c in full if c.startswith("politics_")] + [c for c in ("fred_epu", "fred_emv_epu") if c in full]:
        base = full[c].rolling(60, min_periods=20)
        f[c + "_z"] = (full[c].rolling(2, min_periods=1).mean() - base.mean()) / (base.std() + 1e-9)
    for c in [c for c in full if c.startswith("fred_")]:
        f[c] = full[c]
        f[c + "_chg5"] = full[c] - full[c].shift(7)
    for c in [c for c in full if c.startswith("mkt_")]:
        if c == "mkt_vix":
            f["mkt_vix"] = full[c]
            f["mkt_vix_chg"] = full[c].pct_change(1, fill_method=None)
        else:
            f[c + "_ret1"] = full[c].pct_change(1, fill_method=None)
            f[c + "_ret5"] = full[c].pct_change(7, fill_method=None)
    # composite "political world" indices: average attention / tone across the political themes
    # political world = policy uncertainty + geopolitical risk (+ GDELT political themes when available)
    pol_z = [c for c in ("fred_epu_z", "politics_gpr_z", "politics_gpr_acts_z", "politics_gpr_threats_z") if c in f]
    if pol_z:
        f["world_politics_risk"] = f[pol_z].mean(axis=1)
    pol = ["election", "legislation", "sanctions", "conflict", "terror", "protest", "regulation", "tariffs"]
    eco = ["inflation", "rates", "centralbank", "jobs", "trade_dispute", "oil", "debt", "banking"]
    for name, group in (("politics", pol), ("economy", eco)):
        a = [f"news_{g}_attn" for g in group if f"news_{g}_attn" in f]
        t = [f"news_{g}_tone" for g in group if f"news_{g}_tone" in f]
        if a:
            f[f"world_{name}_attn"] = f[a].mean(axis=1)
        if t:
            f[f"world_{name}_tone"] = f[t].mean(axis=1)
    # known at 09:00 ET on t = everything through calendar day t-1
    f = f.shift(1)
    return f.reindex(dates, method="ffill")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2017-01-01")
    ap.add_argument("--no-gdelt", action="store_true")
    a = ap.parse_args()
    raw = build(a.start, use_gdelt=not a.no_gdelt)
    print(f"macro: {raw.shape[0]} days x {raw.shape[1]} series, {raw.index.min().date()} -> {raw.index.max().date()}")
