"""Per-stock model accuracy from the walk-forward (out-of-sample) predictions.

For every ticker, over the days the model predicted it without having seen them:

  rel_accuracy     share of days the model called the stock's move *relative to the market* correctly
                   (the model's actual target: will it beat or lag the average stock, open to close)
  up_accuracy      share of days it called raw up/down correctly, next to the stock's own up-day rate
  strong_accuracy  same, only on days the stock was in the top or bottom 10% of that day's predictions
                   (the days the model would actually trade it)
  z                how many standard errors rel_accuracy is from a coin flip (|z| > 2 ~ unlikely by chance;
                   with ~2,600 stocks expect ~5% to pass by luck alone)
  signal_bps       average daily return of following the model's sign on the relative move, before costs
  ic               rank correlation between prediction and outcome over time
  5-day versions   the same for the 5-session model
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _z(acc: float, n: int) -> float:
    return float((acc - 0.5) * np.sqrt(n) / 0.5) if n else 0.0


def per_ticker(sig: pd.DataFrame, min_days: int = 120) -> tuple[list[dict], dict[str, dict]]:
    s = sig[sig["z_oc"].notna()].copy()
    day = s.index.get_level_values("date")
    s["rel"] = s["r_oc"] - s.groupby(level="date")["r_oc"].transform("mean")
    liq = s["liquid"]
    s["rk"] = s["mu"].where(liq).groupby(level="date").rank(pct=True)
    s["ok_rel"] = (np.sign(s["mu"]) == np.sign(s["rel"])).astype(float)
    s["ok_up"] = ((s["p_cal"] >= 0.5) == (s["r_oc"] > 0)).astype(float)
    s["strong"] = (s["rk"] >= 0.9) | (s["rk"] <= 0.1)
    s["sig_ret"] = np.sign(s["mu"]) * s["rel"]
    has5 = "mu5" in s and "r_fwd5" in s
    if has5:
        s["rel5"] = s["r_fwd5"] - s.groupby(level="date")["r_fwd5"].transform("mean")
        s["ok5"] = (np.sign(s["mu5"]) == np.sign(s["rel5"])).astype(float).where(s["rel5"].notna())
        s["sig5"] = np.sign(s["mu5"]) * s["rel5"]
    s["year"] = day.year
    table, detail = [], {}
    for tk, g in s.groupby(level="ticker"):
        if len(g) < min_days:
            continue
        g = g.droplevel("ticker")
        st = g[g["strong"]]
        n = len(g)
        row = dict(ticker=tk, kind="stock", days=int(n), liquid_share=float(g["liquid"].mean()),
                   rel_accuracy=float(g["ok_rel"].mean()), z=_z(float(g["ok_rel"].mean()), n),
                   up_accuracy=float(g["ok_up"].mean()), up_rate=float((g["r_oc"] > 0).mean()),
                   strong_n=int(len(st)), strong_accuracy=float(st["ok_rel"].mean()) if len(st) >= 20 else None,
                   strong_bps=float(st["sig_ret"].mean() * 1e4) if len(st) >= 20 else None,
                   signal_bps=float(g["sig_ret"].mean() * 1e4),
                   ic=float(g["mu"].corr(g["rel"], method="spearman")))
        if has5:
            g5 = g.iloc[::5].dropna(subset=["ok5"])            # non-overlapping 5-day periods
            if len(g5) >= 30:
                row.update(acc_5d=float(g5["ok5"].mean()), z_5d=_z(float(g5["ok5"].mean()), len(g5)),
                           signal_5d_bps=float(g5["sig5"].mean() * 1e4), n_5d=int(len(g5)))
        table.append(row)
        step = max(1, n // 160)
        cum = g["sig_ret"].cumsum()
        by_year = g.groupby("year").agg(acc=("ok_rel", "mean"), n=("ok_rel", "size"), bps=("sig_ret", "mean"))
        detail[tk] = dict(
            **{k: v for k, v in row.items() if k not in ("ticker", "kind")},
            by_year=[dict(year=int(y), accuracy=float(r.acc), n=int(r.n), bps=float(r.bps * 1e4))
                     for y, r in by_year.iterrows()],
            curve=[dict(d=str(i.date()), signal=float(v), stock=float(b)) for i, v, b in
                   zip(g.index[::step], cum.values[::step], g["r_oc"].cumsum().values[::step])],
            rolling=[dict(d=str(i.date()), acc=float(v)) for i, v in
                     g["ok_rel"].rolling(120, min_periods=60).mean().dropna().iloc[::step].items()])
    return table, detail


def summary(table: list[dict]) -> dict:
    t = pd.DataFrame(table)
    if t.empty:
        return {}
    liq = t[t["liquid_share"] > 0.5]
    sig_pos, sig_neg = int((t["z"] > 2).sum()), int((t["z"] < -2).sum())
    return dict(stocks=int(len(t)), median_accuracy=float(t["rel_accuracy"].median()),
                mean_accuracy=float(t["rel_accuracy"].mean()),
                liquid_median_accuracy=float(liq["rel_accuracy"].median()) if len(liq) else None,
                strong_median_accuracy=float(t["strong_accuracy"].dropna().median()),
                above_55=int((t["rel_accuracy"] > 0.55).sum()), below_45=int((t["rel_accuracy"] < 0.45).sum()),
                significant_good=sig_pos, significant_bad=sig_neg,
                expected_by_chance=float(len(t) * 0.0228),          # one-sided 2-sigma tail of a coin flip
                hist=np.histogram(t["rel_accuracy"], bins=np.linspace(0.44, 0.56, 25))[0].tolist(),
                hist_edges=np.linspace(0.44, 0.56, 25).round(4).tolist())
