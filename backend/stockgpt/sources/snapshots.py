"""Daily snapshots that build the history free data does not have.

Historical options prices, premarket prints and consensus estimates are sold by vendors. Their
*current* values are public, so StockGPT records them every day; after a few months the backtest
can use real IV, real premarket gaps and real consensus surprises instead of estimates.

    python -m stockgpt.sources.snapshots premarket    # weekdays 08:50-09:00 ET (before the scan)
    python -m stockgpt.sources.snapshots close        # weekdays after 16:15 ET: options + estimates
    python -m stockgpt.sources.snapshots backfill     # once: the last ~60 days of premarket prints

Outputs (data/raw/snapshots/):
  premarket/<date>.parquet   ticker, premarket (last trade before 09:00 ET)
  options/<date>.parquet     ticker, iv30, iv_weekly, option_volume, atm_spread_pct (end of day)
  estimates/<date>.parquet   ticker, period, eps_avg, eps_low, eps_high, analysts
"""
from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd

from ..config import DATA_DIR

SNAP = DATA_DIR / "snapshots"
ET = "America/New_York"


def liquid_tickers(n: int = 600) -> list[str]:
    px = pd.read_parquet(DATA_DIR / "prices.parquet")
    px = px[px["date"] >= px["date"].max() - pd.Timedelta(days=40)]
    dv = (px["close"] * px["volume"]).groupby(px["ticker"]).mean().sort_values(ascending=False)
    t = pd.read_parquet(DATA_DIR / "tickers.parquet")["ticker"] if (DATA_DIR / "tickers.parquet").exists() else dv.index
    return [x for x in dv.index if x in set(t)][:n]


def _ymap(tickers):
    return {t.replace(".", "-"): t for t in tickers}


def premarket_prints(tickers: list[str], days: str = "1d", cutoff: str = "09:00") -> pd.DataFrame:
    """Last premarket trade before `cutoff` ET for each day in the window."""
    import yfinance as yf
    ym = _ymap(tickers)
    rows = []
    syms = list(ym)
    for i in range(0, len(syms), 100):
        raw = yf.download(syms[i:i + 100], period=days, interval="5m", prepost=True, progress=False,
                          threads=False, auto_adjust=False)
        if raw is None or raw.empty:
            continue
        close = raw["Close"]
        idx = close.index.tz_convert(ET)
        close.index = idx
        pre = close[(idx.time >= pd.Timestamp("04:00").time()) & (idx.time < pd.Timestamp(cutoff).time())]
        last = pre.groupby(pre.index.normalize()).last()
        for d, r in last.iterrows():
            for y, v in r.dropna().items():
                rows.append((d.tz_localize(None), ym.get(y, y), float(v)))
        time.sleep(1.0)
    return pd.DataFrame(rows, columns=["date", "ticker", "premarket"])


def _atm(chain, spot):
    def side(df):
        if df is None or df.empty:
            return np.nan, np.nan, 0.0
        k = (df["strike"] - spot).abs().idxmin()
        r = df.loc[k]
        mid = (r["bid"] + r["ask"]) / 2
        spread = (r["ask"] - r["bid"]) / mid if mid > 0 else np.nan
        return float(r["impliedVolatility"]), float(spread), float(df["volume"].fillna(0).sum())
    ci, cs, cv = side(chain.calls)
    pi, ps, pv = side(chain.puts)
    return np.nanmean([ci, pi]), np.nanmean([cs, ps]), cv + pv


def options_summary(tickers: list[str], pause: float = 0.3) -> pd.DataFrame:
    import yfinance as yf
    today = pd.Timestamp.now(tz=ET).normalize().tz_localize(None)
    rows = []
    for tk in tickers:
        try:
            t = yf.Ticker(tk.replace(".", "-"))
            exps = [pd.Timestamp(e) for e in t.options]
            if not exps:
                continue
            spot = float(t.fast_info["last_price"])
            front = next((e for e in exps if (e - today).days >= 2), exps[0])
            m30 = min(exps, key=lambda e: abs((e - today).days - 30))
            ivw, spw, vw = _atm(t.option_chain(front.strftime("%Y-%m-%d")), spot)
            if m30 != front:
                iv30, sp30, v30 = _atm(t.option_chain(m30.strftime("%Y-%m-%d")), spot)
            else:
                iv30, sp30, v30 = ivw, spw, 0.0
            rows.append(dict(date=today, ticker=tk, iv30=iv30, iv_weekly=ivw, option_volume=vw + v30,
                             atm_spread_pct=spw, front_expiry=front, spot=spot))
        except Exception:
            continue
        time.sleep(pause)
    return pd.DataFrame(rows)


def estimates(tickers: list[str], pause: float = 0.3) -> pd.DataFrame:
    import yfinance as yf
    today = pd.Timestamp.now(tz=ET).normalize().tz_localize(None)
    rows = []
    for tk in tickers:
        try:
            e = yf.Ticker(tk.replace(".", "-")).earnings_estimate
            if e is None or e.empty:
                continue
            for period, r in e.iterrows():
                rows.append(dict(date=today, ticker=tk, period=period, eps_avg=r.get("avg"), eps_low=r.get("low"),
                                 eps_high=r.get("high"), analysts=r.get("numberOfAnalysts")))
        except Exception:
            continue
        time.sleep(pause)
    return pd.DataFrame(rows)


def _save(df: pd.DataFrame, kind: str, by_date: bool = True):
    folder = SNAP / kind
    folder.mkdir(parents=True, exist_ok=True)
    if df.empty:
        return 0
    for d, g in df.groupby("date") if by_date else [(df["date"].max(), df)]:
        g.to_parquet(folder / f"{pd.Timestamp(d):%Y-%m-%d}.parquet", index=False)
    return len(df)


def load(kind: str) -> pd.DataFrame:
    folder = SNAP / kind
    files = sorted(folder.glob("*.parquet")) if folder.exists() else []
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True) if files else pd.DataFrame()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["premarket", "close", "backfill"])
    ap.add_argument("--n", type=int, default=600, help="how many of the most liquid names")
    a = ap.parse_args()
    names = liquid_tickers(a.n)
    if a.what == "premarket":
        print("premarket rows:", _save(premarket_prints(names, "1d"), "premarket"))
    elif a.what == "backfill":
        print("premarket rows:", _save(premarket_prints(names, "60d"), "premarket"))
    else:
        print("options rows:", _save(options_summary(names), "options"))
        print("estimate rows:", _save(estimates(names), "estimates"))
