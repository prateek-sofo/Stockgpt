"""Merge every source into the StockGPT data contract (data/raw/<table>.parquet).

  sec_tickers / sec_fundamentals / sec_earnings   <- python -m stockgpt.sources.sec
  prices.parquet                                  <- python -m stockgpt.sources.prices --provider ...
  sofo_news.parquet                               <- python -m stockgpt.sources.sofo_world
  options.parquet (optional)                      <- vendor options summary (ORATS/ThetaData/Polygon)

    python -m stockgpt.sources.assemble --start 2018-01-01
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from ..config import DATA_DIR


def assemble(start: str = "2019-01-01", min_price: float = 3.0, min_history: int = 120,
             ever_dollar_volume: float = 20e6) -> dict:
    t = pd.read_parquet(DATA_DIR / "sec_tickers.parquet")
    # operating companies only: an SEC industry code, and not a blank-check shell (6770), commodity pool /
    # ETP (6221) or unit investment trust (6726)
    sic = pd.to_numeric(t["sic"], errors="coerce")
    t = t[sic.notna() & ~sic.isin([6770, 6221, 6726])]
    # One registrant with many tickers is an ETN issuer (e.g. a bank's notes) or a stack of preferreds:
    # keep its primary ticker. Two or three share classes (GOOG/GOOGL, BRK.A/BRK.B) are kept as listed.
    n = t.groupby("cik")["ticker"].transform("size")
    t = t[(n <= 3) | ~t.duplicated("cik")]
    px = pd.read_parquet(DATA_DIR / "prices.parquet")
    px["date"] = pd.to_datetime(px["date"]).dt.normalize().astype("datetime64[ns]")
    px = px[(px["date"] >= start) & px["ticker"].isin(t["ticker"])]
    # common stocks with enough history and a tradable price
    # Names that were ever tradable size (60d avg $ volume). This only bounds the dataset; whether a
    # name is tradable on a given day is decided point-in-time by universe.liquid_mask.
    px["dv"] = px["close"] * px["volume"]
    dv60 = px.groupby("ticker")["dv"].transform(lambda x: x.rolling(60, min_periods=20).mean())
    peak = dv60.groupby(px["ticker"]).max()
    stats = px.groupby("ticker").agg(n=("close", "size"), last=("close", "last"))
    keep = stats[(stats["n"] >= min_history) & (stats["last"] >= min_price)
                 & (peak.reindex(stats.index) >= ever_dollar_volume)].index
    px = px[px["ticker"].isin(keep)].sort_values(["date", "ticker"])

    fund = pd.read_parquet(DATA_DIR / "sec_fundamentals.parquet")
    fund = fund[fund["ticker"].isin(keep)].copy()
    fund["report_date"] = pd.to_datetime(fund["report_date"]).astype("datetime64[ns]")
    # P/E at the filing date: close that day / trailing-4-quarter EPS
    fund = fund.sort_values(["ticker", "period_end"])
    fund["eps_ttm"] = fund.groupby("ticker")["eps"].transform(lambda s: s.rolling(4, min_periods=4).sum())
    close = px[["date", "ticker", "close"]].sort_values("date")
    fund = pd.merge_asof(fund.sort_values("report_date"), close, left_on="report_date", right_on="date",
                         by="ticker", direction="backward").drop(columns="date")
    fund["pe_ratio"] = (fund["close"] / fund["eps_ttm"]).where(fund["eps_ttm"] > 0).clip(0, 300)
    fund["shares"] = fund.groupby("ticker")["shares"].ffill() if "shares" in fund else np.nan
    last_shares = fund.sort_values("report_date").groupby("ticker")["shares"].last()
    fund = fund[["ticker", "report_date", "revenue_growth_yoy", "gross_margin", "operating_margin",
                 "net_margin", "fcf_margin", "eps_growth_yoy", "net_debt_to_revenue", "pe_ratio", "shares"]]

    earn = pd.read_parquet(DATA_DIR / "sec_earnings.parquet")
    earn = earn[earn["ticker"].isin(keep)].copy()
    # Surprise vs. the same quarter a year earlier (seasonal random-walk estimate). Replace with
    # consensus estimates when a vendor feed is connected.
    g = earn["eps_surprise_proxy"]
    earn["eps_estimate"] = earn["eps_actual"] / (1 + g.where(g.abs() < 5))
    earn = earn[["ticker", "report_date", "timing", "eps_actual", "eps_estimate"]]

    news_path = DATA_DIR / "sofo_news.parquet"
    if news_path.exists():
        news = pd.read_parquet(news_path)
        news = news[news["ticker"].isin(keep)][["published_at", "ticker", "headline", "body", "source", "url"]]
    else:
        news = pd.DataFrame(columns=["published_at", "ticker", "headline", "body", "source", "url"])

    tick = t[t["ticker"].isin(keep)].drop_duplicates("ticker")
    last_px = px.groupby("ticker")["close"].last()
    tick = tick.assign(market_cap=tick["ticker"].map(last_shares) * tick["ticker"].map(last_px))
    out = dict(tickers=tick[["ticker", "name", "sector", "industry", "sic", "market_cap"]],
               prices=px[["date", "ticker", "open", "high", "low", "close", "volume"]],
               fundamentals=fund, earnings=earn, news=news)
    opt = DATA_DIR / "options_vendor.parquet"
    if opt.exists():
        out["options"] = pd.read_parquet(opt)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-01-01")
    a = ap.parse_args()
    for name, df in assemble(a.start).items():
        df.to_parquet(DATA_DIR / f"{name}.parquet", index=False)
        print(f"{name:13s} {len(df):>10,} rows  {df['ticker'].nunique() if 'ticker' in df else '':>6} tickers")
