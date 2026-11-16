"""Which feature groups actually predict next-session direction? Holdout ablation.

Train on dates before --split, test after it. Add one feature group at a time and report, on the
test period only:
  ic        mean daily Spearman correlation between prediction and open->close return (liquid names)
  ic_t      ic / std * sqrt(days): how many standard errors from zero
  spread    average top-decile minus bottom-decile open->close return, bps/day
  hit_top   share of top-decile names that closed up (direction accuracy where it matters)

    python -m stockgpt.ablation --split 2024-01-01
"""
from __future__ import annotations

import argparse
import json

import lightgbm as lgb
import numpy as np
import pandas as pd

from .config import ARTIFACT_DIR
from .data.loader import load_all
from .features import build_panel, feature_columns
from .universe import liquid_mask

GROUPS = {
    "price": lambda c: c.startswith(("gap_", "ret_", "rv20", "atr", "rel_volume", "log_adv", "dist_", "oc_autocorr",
                                     "mkt_gap", "mkt_ret_1d", "sector_gap", "iv", "atm_spread", "event_premium",
                                     "tr_gap", "tx_gap", "tr_ret_1d", "tx_ret_1d", "log_mcap")),
    "earnings+fundamentals": lambda c: c.startswith(("eps_", "is_earnings", "days_", "hist_earn", "f_",
                                                     "tr_eps", "tx_eps")),
    "company news": lambda c: c.startswith(("news_on", "news_prior", "news_5d", "news_abnormal", "sent_", "ev_",
                                            "tr_news", "tx_news")),
    "competitors+peers": lambda c: c.startswith(("peer_", "supply_", "tr_peer", "tx_peer")),
    "macro+political world": lambda c: c.startswith(("fred_", "politics_", "world_", "mkt_", "sector_etf",
                                                     "tr_fred", "tx_fred", "tr_world", "tx_world"))
                                       and c not in ("mkt_gap", "mkt_ret_1d"),
}


def evaluate(test: pd.DataFrame, pred: np.ndarray) -> dict:
    d = test.assign(pred=pred)
    ic = d.groupby(level="date").apply(lambda g: g["pred"].corr(g["r_oc"], method="spearman")).dropna()
    def dec(g):
        q = g["pred"].rank(pct=True)
        top, bot = g[q > 0.9], g[q <= 0.1]
        return pd.Series({"spread": top["r_oc"].mean() - bot["r_oc"].mean(), "hit_top": (top["r_oc"] > 0).mean(),
                          "hit_bot": (bot["r_oc"] < 0).mean()})
    s = d.groupby(level="date").apply(dec)
    return dict(ic=float(ic.mean()), ic_t=float(ic.mean() / ic.std() * np.sqrt(len(ic))),
                spread_bps=float(s["spread"].mean() * 1e4), hit_top=float(s["hit_top"].mean()),
                hit_bottom=float(s["hit_bot"].mean()), days=int(len(ic)))


DATE_LEVEL = ("fred_", "politics_", "world_", "mkt_vix", "mkt_spx", "mkt_dxy", "mkt_oil", "mkt_gold", "mkt_gap",
              "mkt_ret_1d")


def market_model(panel: pd.DataFrame, feats: list[str], split: str) -> dict:
    """Does the macro/political world predict the *market's* open->close direction? (time series)"""
    day = panel.index.get_level_values("date")
    liq = liquid_mask(panel)
    mret = panel.loc[liq.values, "r_oc"].groupby(level="date").mean()
    cols = [c for c in feats if c.startswith(DATE_LEVEL)]
    X = panel[cols].groupby(level="date").first()
    d = X.join(mret.rename("y")).dropna(subset=["y"])
    tr, te = d[d.index < split], d[d.index >= split]
    from sklearn.linear_model import Ridge
    out = {}
    for name, model in (("ridge", Ridge(alpha=50.0)),
                        ("gbm", lgb.LGBMRegressor(n_estimators=300, learning_rate=0.02, num_leaves=7,
                                                  min_child_samples=40, subsample=0.8, subsample_freq=1,
                                                  colsample_bytree=0.7, verbose=-1))):
        Xtr = tr[cols].fillna(0.0)
        mu, sd = Xtr.mean(), Xtr.std() + 1e-9
        model.fit((Xtr - mu) / sd, tr["y"])
        p = model.predict((te[cols].fillna(0.0) - mu) / sd)
        hit = float((np.sign(p) == np.sign(te["y"])).mean())
        strat = np.sign(p) * te["y"]
        out[name] = dict(hit=hit, corr=float(np.corrcoef(p, te["y"])[0, 1]),
                         ann_ret=float(strat.mean() * 252), sharpe=float(strat.mean() / strat.std() * np.sqrt(252)),
                         base_up_rate=float((te["y"] > 0).mean()), days=int(len(te)))
    return dict(features=len(cols), **out)


def run(split: str = "2024-01-01", max_train: int = 1_500_000) -> list[dict]:
    tables, src = load_all()
    panel = build_panel(tables)
    panel = panel[panel["z_oc"].notna()]
    feats = feature_columns(panel)
    day = panel.index.get_level_values("date")
    train = panel[day < pd.Timestamp(split) - pd.Timedelta(days=2)]
    if len(train) > max_train:
        train = train.iloc[-max_train:]
    test = panel[day >= pd.Timestamp(split)]
    test = test[liquid_mask(test).values]
    # stock-relative target: the day's cross-sectional average removed (what ranking long/short trades)
    for df in (train,):
        df["z_x"] = df["z_oc"] - df.groupby(level="date")["z_oc"].transform("mean")
    params = dict(objective="huber", alpha=1.5, n_estimators=400, learning_rate=0.02, num_leaves=15,
                  min_child_samples=1000, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                  reg_lambda=5.0, verbose=-1, n_jobs=4)
    results = []
    for target in ("z_oc", "z_x"):
        used = []
        for name, rule in GROUPS.items():
            add = [c for c in feats if rule(c) and c not in used]
            if not add or train[add].std().fillna(0).max() == 0:
                results.append(dict(target=target, step=f"+ {name}", added=0, note="no data for this group yet"))
                continue
            used += add
            m = lgb.LGBMRegressor(**params).fit(train[used].astype(np.float32), train[target])
            r = evaluate(test, m.predict(test[used].astype(np.float32)))
            imp = pd.Series(m.booster_.feature_importance("gain"), index=used)
            r.update(target=target, step=f"+ {name}", added=len(add), features=len(used),
                     top_new=[k for k in (imp[add] / imp.sum()).sort_values(ascending=False).head(4).index])
            results.append(r)
            print(json.dumps(r), flush=True)
    mkt = market_model(panel, feats, split)
    print(json.dumps(dict(market_model=mkt)), flush=True)
    (ARTIFACT_DIR / "ablation.json").write_text(json.dumps(dict(split=split, source=src, results=results,
                                                                market_model=mkt), indent=1))
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="2024-01-01")
    run(ap.parse_args().split)
