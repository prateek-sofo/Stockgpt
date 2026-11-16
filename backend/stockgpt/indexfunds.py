"""Index-fund direction models (SPY, QQQ, IWM, DIA, sector SPDRs, TLT, GLD).

A stock model ranks stocks against each other; an index fund has no cross-section, so this is a
time-series problem: will the fund close above its open (1 session) / be higher in 5 sessions?

Features (all through the previous close): the fund's own momentum, reversal, intraday/overnight
persistence, volatility state, distance from highs; VIX; the political world and macro series
(policy uncertainty, geopolitical risk, rates, credit); calendar effects (day of week, turn of month).

Walk-forward: expanding window from 2010, first prediction after 3 years, refit every 63 sessions,
6-session purge. One pooled model across funds (fund identity as a feature) so each fund borrows
strength from the others.

    python -m stockgpt.indexfunds        # writes artifacts/indexfunds.json
"""
from __future__ import annotations

import json

import lightgbm as lgb
import numpy as np
import pandas as pd

from .config import ARTIFACT_DIR, DATA_DIR

FUNDS = {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "IWM": "Russell 2000", "DIA": "Dow 30",
         "XLK": "Technology", "XLF": "Financials", "XLV": "Healthcare", "XLE": "Energy", "XLI": "Industrials",
         "XLY": "Consumer Disc.", "XLP": "Consumer Staples", "XLB": "Materials", "XLU": "Utilities",
         "XLRE": "Real Estate", "XLC": "Communication", "TLT": "20y Treasuries", "GLD": "Gold"}


def load_prices(start: str = "2010-01-01") -> pd.DataFrame:
    path = DATA_DIR / "indexfunds.parquet"
    if path.exists():
        old = pd.read_parquet(path)
        if pd.Timestamp(old["date"].max()) >= pd.Timestamp.today().normalize() - pd.Timedelta(days=4):
            return old
    import yfinance as yf
    raw = yf.download(list(FUNDS), start=start, auto_adjust=True, progress=False, threads=False)
    df = raw.stack(level=1, future_stack=True).reset_index()
    df.columns = [str(c).lower() for c in df.columns]
    df = df.dropna(subset=["close"])[["date", "ticker", "open", "high", "low", "close", "volume"]]
    df.to_parquet(path, index=False)
    return df


def build(px: pd.DataFrame) -> pd.DataFrame:
    W = lambda col: px.pivot(index="date", columns="ticker", values=col).sort_index()
    o, h, l, c = W("open"), W("high"), W("low"), W("close")
    pc = c.shift(1)
    lr = np.log(c / pc)
    sig = lr.ewm(span=20, min_periods=10).std().shift(1)
    oc, on = np.log(c / o), np.log(o / pc)
    f = {}
    for w in (1, 5, 20, 60):
        f[f"ret_{w}"] = np.log(pc / c.shift(w + 1)) / (sig * np.sqrt(w))
    for w in (5, 20):
        f[f"intra_{w}"] = oc.rolling(w).mean().shift(1) / sig
        f[f"over_{w}"] = on.rolling(w).mean().shift(1) / sig
    f["rv20"] = sig * np.sqrt(252)
    f["vol_5_60"] = lr.rolling(5).std().shift(1) / lr.rolling(60).std().shift(1)
    f["dist_52w_high"] = pc / h.rolling(252, min_periods=120).max().shift(1) - 1
    f["above_200d"] = pc / c.rolling(200, min_periods=120).mean().shift(1) - 1      # trend filter (Faber)
    lo20, hi20 = l.rolling(20).min().shift(1), h.rolling(20).max().shift(1)
    f["rsv_20"] = (pc - lo20) / (hi20 - lo20).replace(0, np.nan)
    panel = pd.concat({k: v.stack(future_stack=True) for k, v in f.items()}, axis=1)
    panel.index.names = ["date", "ticker"]
    dates = c.index
    # macro / political world, lagged to the prior day
    mp = DATA_DIR / "macro_raw.parquet"
    if mp.exists():
        from .sources.macro import daily_features
        raw = pd.read_parquet(mp)
        mf = daily_features(raw, dates)
        keep = [x for x in ("world_politics_risk", "fred_epu_z", "politics_gpr_z", "politics_gpr_threats_z",
                            "fred_dgs10_chg5", "fred_curve_10_2", "fred_hy_spread", "fred_hy_spread_chg5",
                            "mkt_vix", "mkt_vix_chg", "mkt_dxy_ret1", "mkt_oil_ret1") if x in mf]
        pos = dates.get_indexer(panel.index.get_level_values("date"))
        for col in keep:
            panel[col] = mf[col].values[pos]
    d = panel.index.get_level_values("date")
    panel["dow"] = d.dayofweek
    dom = pd.Series(d.day, index=panel.index)
    panel["turn_of_month"] = ((dom >= 28) | (dom <= 3)).astype(int).values       # Ariel / Lakonishok-Smidt
    panel["fund"] = pd.Categorical(panel.index.get_level_values("ticker")).codes
    tg = pd.concat({"r_oc": oc.stack(future_stack=True), "r_cc": lr.stack(future_stack=True),
                    "r_fwd5": np.log(c.shift(-4) / o).stack(future_stack=True),
                    "sig_d": sig.stack(future_stack=True)}, axis=1)
    panel = panel.join(tg)
    return panel[panel["rv20"].notna()].sort_index()


TARGETS = {"1d": "r_oc", "5d": "r_fwd5"}


def walk_forward(panel: pd.DataFrame, min_train: int = 756, every: int = 63, purge: int = 6) -> pd.DataFrame:
    feats = [c for c in panel.columns if c not in ("r_oc", "r_cc", "r_fwd5", "sig_d")]
    dates = panel.index.get_level_values("date").unique().sort_values()
    day = panel.index.get_level_values("date")
    out = []
    for b in range(min_train, len(dates), every):
        tr = panel[day < dates[b - purge]]
        te = panel[day.isin(dates[b:b + every])]
        res = pd.DataFrame(index=te.index)
        for name, tcol in TARGETS.items():
            t = tr[tr[tcol].notna()]
            y = (t[tcol] > 0).astype(int)
            m = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.02, num_leaves=7, min_child_samples=150,
                                   subsample=0.8, subsample_freq=1, colsample_bytree=0.7, reg_lambda=10.0,
                                   verbose=-1, n_jobs=4).fit(t[feats], y)
            res[f"p_{name}"] = m.predict_proba(te[feats])[:, 1]
            res[f"base_{name}"] = y.mean()                 # share of up periods the model had seen
        out.append(res)
    return pd.concat(out).join(panel[["r_oc", "r_fwd5", "sig_d"]])


def _fund_stats(g: pd.DataFrame, h: str, hold: int) -> dict:
    ret = TARGETS[h]
    d = g.dropna(subset=[ret, f"p_{h}"])
    if hold > 1:
        d = d.iloc[::hold]                                  # non-overlapping periods
    up = d[ret] > 0
    pred_up = d[f"p_{h}"] >= 0.5
    acc = float((pred_up == up).mean())
    n = len(d)
    base = float(max(up.mean(), 1 - up.mean()))             # the better of always-up / always-down
    edge = (d[f"p_{h}"] - d[f"base_{h}"]).abs()
    conf = d[edge >= edge.quantile(0.75)]
    cpu = conf[f"p_{h}"] >= conf[f"base_{h}"]
    sig_ret = np.where(pred_up, 1, -1) * d[ret]
    yr = lambda s: {int(k): round(float(v), 4) for k, v in s.items()}
    per = 252 / hold
    return dict(n=int(n), accuracy=acc, up_rate=float(up.mean()), best_naive=base, edge_vs_naive=acc - base,
                z_vs_naive=float((acc - base) * np.sqrt(n) / np.sqrt(base * (1 - base) + 1e-9)),
                conf_n=int(len(conf)), conf_accuracy=float((cpu == (conf[ret] > 0)).mean()) if len(conf) else None,
                signal_ann=float(sig_ret.mean() * per), buyhold_ann=float(d[ret].mean() * per),
                signal_sharpe=float(sig_ret.mean() / sig_ret.std() * np.sqrt(per)) if sig_ret.std() > 0 else 0.0,
                buyhold_sharpe=float(d[ret].mean() / d[ret].std() * np.sqrt(per)),
                by_year=yr((pred_up == up).groupby(d.index.year).mean()),
                curve=[dict(d=str(i.date()), signal=float(a), buyhold=float(b)) for i, a, b in
                       zip(d.index[::max(1, n // 150)], np.cumsum(sig_ret)[::max(1, n // 150)],
                           np.cumsum(d[ret].values)[::max(1, n // 150)])])


def run() -> dict:
    px = load_prices()
    px["date"] = pd.to_datetime(px["date"])
    panel = build(px)
    preds = walk_forward(panel)
    funds = []
    for tk, g in preds.groupby(level="ticker"):
        g = g.droplevel("ticker")
        funds.append(dict(ticker=tk, name=FUNDS.get(tk, tk), kind="index",
                          h1=_fund_stats(g, "1d", 1), h5=_fund_stats(g, "5d", 5),
                          latest=dict(date=str(g.index[-1].date()), p_up_1d=float(g["p_1d"].iloc[-1]),
                                      p_up_5d=float(g["p_5d"].iloc[-1]))))
    dates = preds.index.get_level_values("date")
    res = dict(period=dict(start=str(dates.min().date()), end=str(dates.max().date())), funds=funds,
               method="pooled LightGBM classifier, walk-forward from 2010, refit every 63 sessions, 6-session purge")
    (ARTIFACT_DIR / "indexfunds.json").write_text(json.dumps(res))
    return res


if __name__ == "__main__":
    r = run()
    for f in r["funds"]:
        a, b = f["h1"], f["h5"]
        print(f"{f['ticker']:5s} 1d acc {a['accuracy']:.3f} (naive {a['best_naive']:.3f}, z {a['z_vs_naive']:+.1f}) "
              f"sharpe {a['signal_sharpe']:+.2f} vs b&h {a['buyhold_sharpe']:+.2f} | "
              f"5d acc {b['accuracy']:.3f} (naive {b['best_naive']:.3f}, z {b['z_vs_naive']:+.1f})")
