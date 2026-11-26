"""Two questions, answered on a holdout (train before the split minus a 10-session purge, test after):

1. Does training on a market-adjusted target help? The current target is the stock's move minus the
   average stock's move. A high-beta stock then "beats the market" whenever the market rises, so the
   model can score well by predicting beta. The adjusted target removes beta times the market's move.
2. Do company events help? Every SEC 8-K known before the 09:00 scan, by kind (results, investor
   update, other news, executive change, major agreement, bad news), plus how recently and how often
   the company has been filing.

Each variant is scored two ways: the plain top-25 / bottom-25 book, and the beta-matched book the app
trades. Returns are per day after 6 bps round trip.

    python -m stockgpt.research_events --split 2024-01-01
"""
from __future__ import annotations

import argparse
import json

import lightgbm as lgb
import numpy as np
import pandas as pd

from .backtest import pick_sides
from .config import ARTIFACT_DIR, DATA_DIR, SETTINGS
from .data.loader import load_all
from .features import build_panel, feature_columns
from .universe import liquid_mask

PARAMS = dict(objective="huber", alpha=1.5, n_estimators=400, learning_rate=0.02, num_leaves=15, min_child_samples=1000,
              subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=5.0, verbose=-1, n_jobs=4)
COST = 2 * SETTINGS.costs.stock_slippage_bps / 1e4
KINDS = {"results": ["2.02"], "update": ["7.01"], "other": ["8.01"], "exec": ["5.02"], "agreement": ["1.01", "2.01"],
         "bad": ["4.02", "2.06", "3.01", "2.05", "1.03", "2.04"], "capital": ["3.02", "2.03"]}
ET = "America/New_York"


def event_features(index: pd.MultiIndex) -> pd.DataFrame:
    """Per (date, ticker): which kinds of 8-K became public since the last close and before 09:00,
    how many in the past 5 and 20 sessions, and sessions since the last one."""
    ev = pd.read_parquet(DATA_DIR / "events.parquet")
    ev = ev[ev["form"].isin(["8-K", "8-K/A"])].copy()
    local = ev["accepted"].dt.tz_convert(ET)
    known = (ev["session"] == "post") | ((ev["session"] == "pre") & (local.dt.hour < 9))
    ev = ev[known]
    out = pd.DataFrame(index=index)
    for k, codes in KINDS.items():
        hit = ev[ev["items"].apply(lambda s: any(c in s.split(",") for c in codes))]
        key = pd.MultiIndex.from_frame(hit[["date", "ticker"]].drop_duplicates())
        out[f"k8_{k}"] = index.isin(key).astype(np.float32)
    cnt = ev.groupby(["date", "ticker"]).size().rename("n")
    out["k8_any"] = cnt.reindex(index).fillna(0).clip(0, 3).astype(np.float32).values
    wide = out["k8_any"].gt(0).astype(float).unstack("ticker").fillna(0)
    c5, c20 = wide.rolling(5, min_periods=1).sum().shift(1), wide.rolling(20, min_periods=1).sum().shift(1)
    pos = pd.DataFrame(np.broadcast_to(np.arange(len(wide), dtype=float)[:, None], wide.shape), index=wide.index, columns=wide.columns)
    since = (pos - pos.where(wide > 0).ffill().shift(1)).clip(upper=60)
    for name, w in (("k8_n5", c5), ("k8_n20", c20), ("k8_since", since)):
        out[name] = w.stack(future_stack=True).reindex(index).astype(np.float32).values
    return out


def evaluate(test: pd.DataFrame, pred: np.ndarray, k: int = 25) -> dict:
    d = test[["r_oc", "beta_60", "resid"]].assign(mu=pred).dropna(subset=["r_oc", "mu"])
    g = d.groupby(level="date")
    rel = d["r_oc"] - g["r_oc"].transform("mean")
    ic = d.assign(rel=rel).groupby(level="date").apply(lambda x: x["mu"].corr(x["rel"], method="spearman")).dropna()
    icr = g.apply(lambda x: x["mu"].corr(x["resid"], method="spearman")).dropna()
    raw, matched, gap, hits = [], [], [], []
    for day, x in g:
        if len(x) < 100:
            continue
        hi, lo = x.nlargest(k, "mu"), x.nsmallest(k, "mu")
        raw.append((day, (hi["r_oc"].mean() - lo["r_oc"].mean()) / 2 - COST))
        L, S = pick_sides(x, k)
        matched.append((day, (L["r_oc"].mean() - S["r_oc"].mean()) / 2 - COST))
        m = x["r_oc"].mean()
        hits.append(((L["r_oc"] > m).sum() + (S["r_oc"] < m).sum()) / (len(L) + len(S)))
    def book(rows):
        s = pd.Series(dict(rows))
        eq = (1 + s).cumprod()
        return dict(net_bps_day=float(s.mean() * 1e4), sharpe=float(s.mean() / s.std() * np.sqrt(252)), max_dd=float((eq / eq.cummax() - 1).min()),
                    by_year={int(y): round(float(v * 1e4), 1) for y, v in s.groupby(s.index.year).mean().items()})
    return dict(ic=float(ic.mean()), ic_t=float(ic.mean() / ic.std() * np.sqrt(len(ic))), ic_resid=float(icr.mean()),
                ic_resid_t=float(icr.mean() / icr.std() * np.sqrt(len(icr))), raw_book=book(raw), matched_book=book(matched),
                hit_matched=float(np.mean(hits)), days=int(len(ic)))


def run(split: str = "2024-01-01", max_train: int = 1_500_000) -> dict:
    tables, _ = load_all()
    panel = build_panel(tables)
    base = feature_columns(panel)
    # the panel already carries both ingredients (features.build_panel): the market-adjusted target
    # z_b / resid_oc and the k8_* event features. This run measures each against the old setup.
    evc = [c for c in base if c.startswith("k8_")]
    base = [c for c in base if c not in evc]
    panel["resid"] = panel["resid_oc"]
    liq = liquid_mask(panel).values

    day = panel.index.get_level_values("date")
    dates = day.unique().sort_values()
    cut = dates[dates.searchsorted(pd.Timestamp(split)) - 10]
    train = panel[(day < cut) & panel["z_x"].notna()]
    train = train.iloc[-max_train:] if len(train) > max_train else train
    test = panel[(day >= pd.Timestamp(split)) & liq & panel["r_oc"].notna()]
    del panel

    def fit(cols, target, seeds=(0,)):
        tr = train[train[target].notna()]
        preds, model = [], None
        for s in seeds:
            model = lgb.LGBMRegressor(**{**PARAMS, "random_state": s}).fit(tr[cols].astype(np.float32), tr[target])
            preds.append(model.predict(test[cols].astype(np.float32)))
        return model, np.mean(preds, axis=0)

    out = []
    def report(name, note, pred):
        r = dict(variant=name, note=note, **evaluate(test, pred))
        out.append(r)
        print(json.dumps(r), flush=True)

    _, p0 = fit(base, "z_x")
    report("current", "current features, target: stock minus the average stock", p0)
    _, p1 = fit(base, "z_b")
    report("market-adjusted target", "same features, target: stock minus beta times the market", p1)
    _, p2 = fit(base + evc, "z_x")
    report("+ company events", "current target, plus 8-K events known before the scan", p2)
    m3, p3 = fit(base + evc, "z_b")
    report("market-adjusted target + company events", "both changes", p3)
    _, p4 = fit(base + evc, "z_b", seeds=(0, 1, 2))
    report("both, 3-seed ensemble", "both changes, three models averaged", p4)
    # event days only: is there anything to trade on the day after a company event?
    on = test["k8_any"].values > 0
    r = dict(variant="event days only (both changes)", note="scored only on stock-days with an overnight 8-K",
             **evaluate(test[on], p3[on], k=5))
    out.append(r)
    print(json.dumps(r), flush=True)
    imp = pd.Series(m3.booster_.feature_importance("gain"), index=base + evc)
    imp = (imp / imp.sum()).sort_values(ascending=False)
    res = dict(split=split, train_rows=int(len(train)), test_rows=int(len(test)), cost_bps_round_trip=COST * 1e4, variants=out,
               event_feature_share=float(imp[evc].sum()), top_event_features=[dict(feature=k, importance=float(v)) for k, v in imp[evc].sort_values(ascending=False).head(6).items()],
               top_features=[dict(feature=k, importance=float(v)) for k, v in imp.head(12).items()])
    (ARTIFACT_DIR / "research_events.json").write_text(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="2024-01-01")
    run(ap.parse_args().split)
