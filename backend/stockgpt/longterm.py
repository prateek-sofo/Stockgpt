"""Long-term (monthly) ranking: fundamentals quality + growth + value + news trend + momentum.

Backtest: every 21 trading days, buy the top quintile of the liquid universe (equal weight),
hold 21 days, compare with equal-weight universe.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .universe import liquid_mask

FACTORS = {
    "f_revenue_growth_yoy": 1.0, "f_eps_growth_yoy": 1.0, "f_operating_margin": 1.0,
    "f_fcf_margin": 1.0, "f_net_debt_to_ebitda": -0.5, "f_pe_ratio": -0.5,
    "ret_60d_z": 0.5, "news_trend_60": 0.75,
}
LABELS = {"f_revenue_growth_yoy": "Revenue growth", "f_eps_growth_yoy": "EPS growth",
          "f_operating_margin": "Operating margin", "f_fcf_margin": "FCF margin",
          "f_net_debt_to_ebitda": "Leverage (lower better)", "f_pe_ratio": "Valuation (lower P/E better)",
          "ret_60d_z": "3-month momentum", "news_trend_60": "60-day news sentiment"}


def score(panel: pd.DataFrame) -> pd.DataFrame:
    p = panel.copy()
    ws = p["news_on_wsent"].unstack() if "news_on_wsent" in p else None
    if ws is not None:
        p["news_trend_60"] = ws.rolling(60, min_periods=10).mean().shift(1).stack(future_stack=True)
    cols = [c for c in FACTORS if c in p]
    z = p[cols].groupby(level="date").transform(lambda s: (s - s.mean()) / (s.std() + 1e-9)).clip(-3, 3)
    comp = sum(z[c].fillna(0) * w for c, w in FACTORS.items() if c in z) / sum(abs(w) for c, w in FACTORS.items() if c in z)
    out = z.add_prefix("z_")
    out["lt_score"] = comp
    return out


def backtest(panel: pd.DataFrame, hold: int = 21, quantile: float = 0.2) -> dict:
    sc = score(panel).join(panel[["r_fwd20"]]).join(liquid_mask(panel))
    dates = sc.index.get_level_values("date").unique().sort_values()
    rebal = dates[260::hold]
    rows, eq_t, eq_u = [], 1.0, 1.0
    for d in rebal:
        x = sc.xs(d, level="date")
        x = x[x["liquid"] & x["r_fwd20"].notna()]
        if len(x) < 20:
            continue
        top = x[x["lt_score"] >= x["lt_score"].quantile(1 - quantile)]
        rt, ru = float(np.expm1(top["r_fwd20"]).mean()), float(np.expm1(x["r_fwd20"]).mean())
        eq_t *= 1 + rt
        eq_u *= 1 + ru
        rows.append(dict(date=str(d.date()), top=round(eq_t, 4), universe=round(eq_u, 4),
                         ret_top=rt, ret_univ=ru, n=len(top)))
    df = pd.DataFrame(rows)
    if df.empty:
        return {"curve": [], "stats": {}}
    per_year = 252 / hold
    ex = df["ret_top"] - df["ret_univ"]
    stats = dict(periods=len(df), cagr_top=float(eq_t ** (per_year / len(df)) - 1),
                 cagr_universe=float(eq_u ** (per_year / len(df)) - 1),
                 excess_annual=float(ex.mean() * per_year),
                 info_ratio=float(ex.mean() / (ex.std() + 1e-9) * np.sqrt(per_year)),
                 hit_rate=float((ex > 0).mean()))
    return {"curve": df.to_dict("records"), "stats": stats}


def current_ranking(panel: pd.DataFrame, tickers: pd.DataFrame, top: int = 40) -> list[dict]:
    sc = score(panel)
    last = sc.index.get_level_values("date").max()
    x = sc.xs(last, level="date").join(liquid_mask(panel).xs(last, level="date"))
    x = x[x["liquid"]].sort_values("lt_score", ascending=False).head(top)
    meta = tickers.set_index("ticker")
    out = []
    for tk, r in x.iterrows():
        drivers = sorted(((LABELS[c[2:]], float(r[c]) * np.sign(FACTORS[c[2:]]))
                          for c in x.columns if c.startswith("z_") and c[2:] in FACTORS and pd.notna(r[c])),
                         key=lambda t: -abs(t[1]))[:4]
        out.append(dict(ticker=tk, name=meta.at[tk, "name"], sector=meta.at[tk, "sector"],
                        score=float(r["lt_score"]), drivers=[dict(label=l, z=v) for l, v in drivers]))
    return out
