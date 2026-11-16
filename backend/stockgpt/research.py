"""Model research: do signals from the published literature, a longer horizon and ensembling help?

Holdout design (López de Prado): train on dates before --split minus a 10-session purge (so no 5-day
label overlaps the test period), test after it, liquid names only. Each variant reports:

  ic / t          mean daily rank correlation of prediction with the realised relative return, and its t-stat
  spread_bps      top-decile minus bottom-decile return over the holding period
  net_bps_day     per-day return of a top-25 long / bottom-25 short book after 6 bps round-trip cost per name
  by_year         net_bps_day per calendar year (stability)

    python -m stockgpt.research --split 2024-01-01
"""
from __future__ import annotations

import argparse
import json

import lightgbm as lgb
import numpy as np
import pandas as pd

from .config import ARTIFACT_DIR, SETTINGS
from .data.loader import load_all
from .features import RESEARCH_FEATURES, build_panel, feature_columns
from .universe import liquid_mask

PARAMS = dict(objective="huber", alpha=1.5, n_estimators=400, learning_rate=0.02, num_leaves=15,
              min_child_samples=1000, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
              reg_lambda=5.0, verbose=-1, n_jobs=4)
COST = 2 * SETTINGS.costs.stock_slippage_bps / 1e4


def evaluate(test: pd.DataFrame, pred: np.ndarray, hold: int, k: int = 25) -> dict:
    ret = "r_oc" if hold == 1 else "r_fwd5"
    d = test[[ret]].assign(pred=pred).dropna()
    d["rel"] = d[ret] - d.groupby(level="date")[ret].transform("mean")
    g = d.groupby(level="date")
    ic = g.apply(lambda x: x["pred"].corr(x["rel"], method="spearman")).dropna()
    rk = g["pred"].rank(pct=True)
    spread = (d[rk > 0.9].groupby(level="date")["rel"].mean() - d[rk <= 0.1].groupby(level="date")["rel"].mean())
    hi = g["pred"].rank(ascending=False)
    lo = g["pred"].rank(ascending=True)
    book = (d[hi <= k].groupby(level="date")[ret].mean() - d[lo <= k].groupby(level="date")[ret].mean()) / 2
    net_day = (book - COST) / hold                       # one sleeve per day, each held `hold` sessions
    top_up = (d[hi <= k]["rel"] > 0).mean()
    bot_dn = (d[lo <= k]["rel"] < 0).mean()
    # t-stats use non-overlapping observations for the multi-day horizon
    ic_n = ic.iloc[::hold]
    nd = net_day.iloc[::hold] if hold > 1 else net_day
    return dict(ic=float(ic.mean()), ic_t=float(ic_n.mean() / ic_n.std() * np.sqrt(len(ic_n))),
                spread_bps=float(spread.mean() * 1e4), gross_bps_day=float(book.mean() / hold * 1e4),
                net_bps_day=float(net_day.mean() * 1e4), net_ann=float(net_day.mean() * 252),
                sharpe=float(nd.mean() / nd.std() * np.sqrt(252 / hold)) if nd.std() > 0 else 0.0,
                hit_long=float(top_up), hit_short=float(bot_dn),
                by_year={int(y): round(float(v * 1e4), 2) for y, v in net_day.groupby(net_day.index.year).mean().items()},
                days=int(len(ic)))


def run(split: str = "2024-01-01", max_train: int = 1_500_000) -> dict:
    tables, src = load_all()
    panel = build_panel(tables)
    feats_all = feature_columns(panel)
    base = [c for c in feats_all if c not in RESEARCH_FEATURES]
    day = panel.index.get_level_values("date")
    dates = day.unique().sort_values()
    cut = dates[dates.searchsorted(pd.Timestamp(split)) - 10]         # purge: 10 sessions
    train = panel[(day < cut) & panel["z_x"].notna()]
    train = train.iloc[-max_train:] if len(train) > max_train else train
    test = panel[day >= pd.Timestamp(split)]
    test = test[liquid_mask(test).values]
    del panel

    def fit(cols, target, seed=0):
        tr = train[train[target].notna()]
        m = lgb.LGBMRegressor(**{**PARAMS, "random_state": seed}).fit(tr[cols].astype(np.float32), tr[target])
        return m, m.predict(test[cols].astype(np.float32))

    out = []
    def report(name, pred, hold, note=""):
        r = dict(variant=name, hold_days=hold, note=note, **evaluate(test, pred, hold))
        out.append(r)
        print(json.dumps(r), flush=True)

    _, p_base = fit(base, "z_x")
    report("v2: current features, 1-day", p_base, 1, "price, earnings, fundamentals, competitor news/earnings")
    m1, p1 = fit(feats_all, "z_x")
    report("+ literature signals, 1-day", p1, 1, "tug-of-war, momentum, reversal, MAX, 52w high, Amihud, ivol, beta, Alpha158")
    seeds = [p1] + [fit(feats_all, "z_x", s)[1] for s in (1, 2)]
    p1e = np.mean(seeds, axis=0)
    report("+ 3-seed ensemble, 1-day", p1e, 1, "average of three models with different random seeds")
    m5, p5 = fit(feats_all, "z5_x")
    report("5-day target, held 5 days", p5, 5, "same features, predicts open t -> close t+4")
    rank = lambda p: pd.Series(p, index=test.index).groupby(level="date").rank(pct=True).values
    report("1-day + 5-day ensemble, held 5 days", rank(p1e) + rank(p5), 5, "rank-average of both horizons")
    report("5-day model, traded daily", p5, 1, "does the slower signal also work at one day?")

    imp = pd.Series(m5.booster_.feature_importance("gain"), index=feats_all)
    imp = (imp / imp.sum()).sort_values(ascending=False)
    res = dict(split=split, source=src, purge_sessions=10, train_rows=int(len(train)), test_rows=int(len(test)),
               cost_bps_round_trip=COST * 1e4, book="top 25 long / bottom 25 short, equal weight",
               variants=out, trials=len(out),
               top_features_5d=[dict(feature=k, importance=float(v), research=k in RESEARCH_FEATURES)
                                for k, v in imp.head(15).items()])
    (ARTIFACT_DIR / "research.json").write_text(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="2024-01-01")
    run(ap.parse_args().split)
