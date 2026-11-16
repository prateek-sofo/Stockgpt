"""Liquid-universe filter: from 4-5k listed names down to the few hundred where options
volume and tight spreads make day trading realistic. Evaluated per day with trailing data."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import SETTINGS


def liquid_mask(panel: pd.DataFrame, cfg=None) -> pd.Series:
    cfg = cfg or SETTINGS.universe
    price_ok = panel["prev_close"] >= cfg.min_price
    adv_ok = (10 ** panel["log_adv20"].fillna(0)) >= cfg.min_avg_dollar_volume
    m = price_ok & adv_ok
    if "log_opt_vol20" in panel:
        m &= (10 ** panel["log_opt_vol20"].fillna(0) - 1) >= cfg.min_avg_option_volume
    if "atm_spread" in panel:
        m &= panel["atm_spread"].fillna(1) <= cfg.max_option_spread_pct
    # cap by dollar volume rank per day
    rank = panel["log_adv20"].where(m).groupby(level="date").rank(ascending=False)
    return (m & (rank <= cfg.max_names)).rename("liquid")


def universe_snapshot(panel: pd.DataFrame, tickers: pd.DataFrame) -> pd.DataFrame:
    last = panel.index.get_level_values("date").max()
    d = panel.xs(last, level="date").copy()
    d["liquid"] = liquid_mask(panel).xs(last, level="date")
    d = d.join(tickers.set_index("ticker")[["name", "sector", "market_cap"]])
    d["adv20"] = 10 ** d["log_adv20"]
    d["opt_vol20"] = 10 ** d.get("log_opt_vol20", np.nan) - 1
    return d
