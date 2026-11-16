"""Feature panel: one row per (trading date, ticker) with only information available at
~09:00 ET on that date (the morning scan time), plus forward targets for training.

Timing rules (no look-ahead):
* prices / options: through the previous session close (t-1), plus the 09:00 premarket print.
* news: published before 09:00 ET on t. Overnight news = since 16:00 ET on t-1. News published
  during the t-1 session is a separate "prior session" feature.
* earnings: a BMO report on t or AMC report on t-1 is known before the open.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import DATA_DIR
from .nlp.sentiment import LexiconScorer

ET = "America/New_York"
EVENTS = ["earnings", "guidance", "analyst", "m&a", "regulatory", "legal", "product", "capital"]


def score_news(news: pd.DataFrame, scorer=None) -> pd.DataFrame:
    """Score unique headlines once (scrapes repeat a lot), then join back."""
    scorer = scorer or LexiconScorer()
    uniq = news["headline"].drop_duplicates()
    scored = scorer.score(uniq.tolist())
    scored.index = uniq.values
    out = news.join(scored, on="headline")
    return out


def _assign_sessions(news: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Map each article to the trading date whose morning scan first sees it."""
    et = news["published_at"].dt.tz_convert(ET)
    local = et.dt.tz_localize(None)
    day = local.dt.normalize()
    hour = local.dt.hour + local.dt.minute / 60
    # 'cutoff' = the first scan (09:00 ET) at or after publication
    scan_day = np.where(hour < 9.0, day, day + pd.Timedelta(days=1))
    scan_day = pd.DatetimeIndex(scan_day)
    pos = dates.searchsorted(scan_day)                     # next trading date >= scan_day
    news = news.assign(_pos=pos, _hour=hour.values, _day=day.values)
    news = news[news["_pos"] < len(dates)]
    news["date"] = dates[news["_pos"].values]
    # overnight = published after the previous session close
    prev_close = np.where(news["_pos"] > 0, dates[np.maximum(news["_pos"] - 1, 0)], news["date"])
    prev_close = pd.DatetimeIndex(prev_close) + pd.Timedelta(hours=16)
    news["overnight"] = (local.loc[news.index].values >= prev_close.values)
    return news.drop(columns=["_pos", "_hour", "_day"])


def news_features(news_scored: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    n = _assign_sessions(news_scored, dates)
    n["wsent"] = n["sentiment"] * (0.3 + n["magnitude"])
    g_on = n[n["overnight"]].groupby(["date", "ticker"])
    f = pd.DataFrame({
        "news_on_count": g_on.size(),
        "news_on_sent_mean": g_on["sentiment"].mean(),
        "news_on_wsent": g_on["wsent"].sum(),
        "news_on_sent_max": g_on["sentiment"].max(),
        "news_on_sent_min": g_on["sentiment"].min(),
        "news_on_mag_max": g_on["magnitude"].max(),
    })
    for ev in EVENTS:
        sub = n[n["overnight"] & (n["event"] == ev)].groupby(["date", "ticker"])["wsent"].sum()
        f[f"ev_{ev}"] = sub
    prior = n[~n["overnight"]].groupby(["date", "ticker"])["wsent"].sum().rename("news_prior_wsent")
    f = f.join(prior, how="outer")
    return f


def build_panel(tables: dict, scorer=None) -> pd.DataFrame:
    prices = tables["prices"].sort_values(["date", "ticker"])
    dates = pd.DatetimeIndex(sorted(prices["date"].unique()), name="date")
    W = lambda col: prices.pivot(index="date", columns="ticker", values=col).reindex(dates)
    o, h, l, c, v = W("open"), W("high"), W("low"), W("close"), W("volume")
    pre = W("premarket") if "premarket" in prices else c.shift(1)
    pc = c.shift(1)

    lr = np.log(c / pc)
    sig20 = lr.ewm(span=20, min_periods=10).std().shift(1)          # known at t
    sig20 = sig20.fillna(lr.expanding(5).std().shift(1))
    feats: dict[str, pd.DataFrame] = {}
    gap = np.log(pre / pc)
    feats["gap_pre"] = gap
    feats["gap_pre_z"] = gap / sig20
    feats["ret_1d_z"] = lr.shift(1) / sig20
    feats["ret_5d_z"] = np.log(pc / c.shift(6)) / (sig20 * np.sqrt(5))
    feats["ret_20d_z"] = np.log(pc / c.shift(21)) / (sig20 * np.sqrt(20))
    feats["ret_60d_z"] = np.log(pc / c.shift(61)) / (sig20 * np.sqrt(60))
    feats["rv20"] = sig20 * np.sqrt(252)
    tr = pd.concat({"a": h - l, "b": (h - pc).abs(), "c": (l - pc).abs()}).groupby(level=1).max()
    tr = tr.reindex(dates)
    feats["atr14_pct"] = (tr.rolling(14).mean() / c).shift(1)
    dv = v * c
    adv20 = dv.rolling(20, min_periods=5).mean().shift(1)
    feats["rel_volume_1d"] = (dv.shift(1) / adv20)
    feats["log_adv20"] = np.log10(adv20)
    feats["dist_20d_high"] = (pc / h.rolling(20).max().shift(1) - 1) / sig20
    feats["dist_20d_low"] = (pc / l.rolling(20).min().shift(1) - 1) / sig20
    feats["oc_autocorr_prev"] = np.log(c / o).shift(1) / sig20      # yesterday's intraday move
    # market / sector context
    mkt_gap = gap.mean(axis=1)
    feats["mkt_gap"] = pd.DataFrame(np.repeat(mkt_gap.values[:, None], gap.shape[1], 1),
                                    index=dates, columns=gap.columns)
    mkt_ret = lr.mean(axis=1).shift(1)
    feats["mkt_ret_1d"] = pd.DataFrame(np.repeat(mkt_ret.values[:, None], gap.shape[1], 1),
                                       index=dates, columns=gap.columns)
    sectors = tables["tickers"].set_index("ticker")["sector"].reindex(gap.columns)
    sec_gap = gap.T.groupby(sectors.values).transform("mean").T
    feats["sector_gap_rel"] = (gap - sec_gap) / sig20

    # options context. Estimated IV (realised vol + a typical 10% variance premium) everywhere; real IV from
    # an options feed or the daily snapshots replaces it wherever it exists.
    rv_long = lr.ewm(span=60, min_periods=20).std() * np.sqrt(252)
    iv_est = np.maximum(sig20 * np.sqrt(252), rv_long.shift(1)) * 1.10
    ivt_est = np.maximum(lr.ewm(span=20, min_periods=10).std() * np.sqrt(252), rv_long) * 1.10
    spread_est = (0.015 + 0.5 / np.sqrt(adv20 / 1e6)).clip(0.015, 0.3)
    iv, ivw_prev, iv_today, spread = iv_est, iv_est, ivt_est, spread_est
    iv_source = "estimated"
    op = tables.get("options")
    if op is not None and len(op):
        op = op.drop_duplicates(["date", "ticker"], keep="last")
        P = lambda col: op.pivot(index="date", columns="ticker", values=col).reindex(index=dates, columns=gap.columns)
        real30 = P("iv30")
        realw = P("iv_weekly") if "iv_weekly" in op else real30
        iv = real30.shift(1).combine_first(iv_est)
        ivw_prev = realw.shift(1).combine_first(iv_est)
        iv_today = realw.combine_first(ivt_est)
        if "atm_spread_pct" in op:
            spread = P("atm_spread_pct").shift(1).combine_first(spread_est)
        ov = P("option_volume")
        feats["opt_vol_rel"] = ov.shift(1) / ov.rolling(20, min_periods=5).mean().shift(1)
        feats["log_opt_vol20"] = np.log10(ov.rolling(20, min_periods=5).mean().shift(1) + 1)
        coverage = real30.notna().to_numpy().mean()
        iv_source = "vendor" if coverage > 0.8 else "mixed"
    feats["iv30"] = iv
    feats["iv_rv"] = iv / feats["rv20"]
    feats["iv_rank"] = iv.rolling(252, min_periods=60).rank(pct=True)
    feats["ivw_prev"] = ivw_prev
    feats["event_premium"] = ivw_prev / iv          # >1 = event priced into the front week
    feats["atm_spread"] = spread

    panel = pd.concat({k: df.stack(future_stack=True).astype(np.float32) for k, df in feats.items()}, axis=1)
    panel.index.names = ["date", "ticker"]
    del feats

    # earnings timing
    e = tables.get("earnings")
    if e is not None and len(e):
        e = e.copy()
        pos = dates.searchsorted(e["report_date"])
        e["react_pos"] = pos + (e["timing"].eq("AMC")).astype(int)
        e = e[e["react_pos"] < len(dates)]
        e["date"] = dates[e["react_pos"].values]
        if {"eps_actual", "eps_estimate"} <= set(e.columns):
            e["eps_surprise"] = (e["eps_actual"] / e["eps_estimate"].replace(0, np.nan) - 1).clip(-1, 1)
        else:
            e["eps_surprise"] = np.nan
        react = e.set_index(["date", "ticker"])[["eps_surprise"]]
        react = react[~react.index.duplicated()]
        panel = panel.join(react)
        panel["is_earnings"] = panel.index.isin(react.index).astype(np.int8)
        # days to next / since last reaction day
        rd = e[["ticker", "react_pos"]]
        dn = np.full((len(dates), len(gap.columns)), 999.0)
        ds = np.full_like(dn, 999.0)
        tt = np.arange(len(dates))
        colpos = {t: j for j, t in enumerate(gap.columns)}
        for tk, grp in rd.groupby("ticker"):
            if tk not in colpos:
                continue
            idx = np.sort(grp["react_pos"].values)
            p = np.searchsorted(idx, tt)                     # next >= t
            ok = p < len(idx)
            dn[ok, colpos[tk]] = idx[p[ok]] - tt[ok]
            q = np.searchsorted(idx, tt) - 1                 # previous < t
            okq = q >= 0
            ds[okq, colpos[tk]] = tt[okq] - idx[q[okq]]
        panel["days_to_earn"] = pd.DataFrame(dn, index=dates, columns=gap.columns).stack().values
        panel["days_since_earn"] = pd.DataFrame(ds, index=dates, columns=gap.columns).stack().values
        # past earnings reaction size for this ticker (|close-to-close| on prior reaction days)
        cc = lr.stack(future_stack=True)
        cc.index.names = ["date", "ticker"]
        er = cc[panel["is_earnings"] == 1].abs().rename("earn_abs")
        er = er.reset_index().sort_values("date")
        er["hist_earn_move"] = er.groupby("ticker")["earn_abs"].transform(
            lambda s: s.shift(1).expanding().mean())
        panel = panel.join(er.set_index(["date", "ticker"])["hist_earn_move"])
        panel["hist_earn_move"] = panel.groupby(level="ticker")["hist_earn_move"].ffill()
    else:
        for col in ("eps_surprise", "is_earnings", "days_to_earn", "days_since_earn", "hist_earn_move"):
            panel[col] = np.nan

    # news
    news = tables["news"]
    if len(news):
        ns = score_news(news, scorer)
        nf = news_features(ns, dates)
        panel = panel.join(nf)
        fill0 = [c for c in nf.columns]
        panel[fill0] = panel[fill0].fillna(0.0)
        cnt = panel["news_on_count"].unstack()
        base = cnt.rolling(60, min_periods=10).mean().shift(1)
        panel["news_abnormal"] = ((cnt - base) / (base + 0.5)).stack(future_stack=True)
        ws = panel["news_on_wsent"].unstack()
        panel["news_5d_wsent"] = ws.rolling(5, min_periods=1).sum().shift(1).stack(future_stack=True)
        panel["sent_x_gap"] = panel["news_on_wsent"] * np.sign(panel["gap_pre"])
        panel["sent_gap_divergence"] = panel["news_on_wsent"] - panel["gap_pre_z"] * 0.3
    else:
        # no news feed connected yet: neutral news features so every downstream step still runs
        for col in ("news_on_count", "news_on_sent_mean", "news_on_wsent", "news_on_sent_max", "news_on_sent_min",
                    "news_on_mag_max", "news_prior_wsent", "news_abnormal", "news_5d_wsent", "sent_x_gap",
                    "sent_gap_divergence", *[f"ev_{e}" for e in EVENTS]):
            panel[col] = 0.0

    # fundamentals (as of the latest report_date < t)
    fund = tables.get("fundamentals")
    if fund is not None and len(fund):
        fcols = [c for c in fund.columns if c not in ("ticker", "report_date")]
        fd = fund.sort_values("report_date").rename(columns={"report_date": "date"})
        left = panel.reset_index()[["date", "ticker"]].sort_values("date")
        merged = pd.merge_asof(left, fd, on="date", by="ticker", allow_exact_matches=False)
        merged = merged.set_index(["date", "ticker"])[fcols].add_prefix("f_")
        panel = panel.join(merged)
        if "f_shares" in panel:
            pcl = pc.stack(future_stack=True).reindex(panel.index)
            panel["log_mcap"] = np.log10((panel["f_shares"] * pcl).where(panel["f_shares"] > 0))
            panel = panel.drop(columns="f_shares")

    # ---------------- competitors and peers ----------------
    from .peers import peer_features
    pf = peer_features(dates, list(gap.columns), lr, panel, tables)
    for col in pf.columns:
        panel[col] = pf[col].values
    del pf

    # ---------------- macro and political world ----------------
    macro_path = DATA_DIR / "macro_raw.parquet"
    if macro_path.exists():
        from .sources.macro import SECTOR_ETF, daily_features
        mf = daily_features(pd.read_parquet(macro_path), dates).astype(np.float32)
        day_pos = dates.get_indexer(panel.index.get_level_values("date"))
        # a focused set: political world, rates/credit, volatility, broad market, dollar, oil, gold
        for col in MACRO_KEEP:
            if col in mf:
                panel[col] = mf[col].values[day_pos]
        # the company's own sector ETF move yesterday (one column instead of eleven)
        sect = tables["tickers"].set_index("ticker")["sector"]
        etf = panel.index.get_level_values("ticker").map(sect).map(SECTOR_ETF)
        sec_ret = np.full(len(panel), np.nan, dtype=np.float32)
        for name in set(SECTOR_ETF.values()):
            col = f"mkt_{name}_ret1"
            if col in mf:
                m = np.asarray(etf == name)
                sec_ret[m] = mf[col].values[day_pos[m]]
        panel["sector_etf_ret1"] = sec_ret
        del mf

    # ---------------- targets ----------------
    targ = {
        "r_oc": np.log(c / o),
        "r_cc": lr,
        "r_gap": np.log(o / pc),
        "r_fwd5": np.log(c.shift(-4) / o),
        "r_fwd20": np.log(c.shift(-19) / o),
        "open_px": o, "close_px": c, "prev_close": pc,
        "high_px": h, "low_px": l,
    }
    targ["ivw_t"] = iv_today
    tg = pd.concat({k: df.stack(future_stack=True) for k, df in targ.items()}, axis=1)
    tg.index.names = ["date", "ticker"]
    panel = panel.join(tg)
    panel["sig_d"] = (panel["rv20"] / np.sqrt(252))
    panel["y_dir"] = (panel["r_oc"] > 0).astype(np.int8)
    panel["z_oc"] = (panel["r_oc"] / panel["sig_d"]).clip(-6, 6)
    # stock-relative target: the day's cross-sectional average removed (what a market-neutral book earns)
    panel["z_x"] = panel["z_oc"] - panel.groupby(level="date")["z_oc"].transform("mean")
    panel = panel.replace([np.inf, -np.inf], np.nan)
    panel = panel[panel["rv20"].notna()].sort_index()
    if iv_source == "estimated" and "hist_earn_move" in panel:
        # front-week IV before a report: price the ticker's own average earnings move (fair-pricing
        # assumption, so estimated-IV straddle backtests carry no built-in edge)
        pre = panel["days_to_earn"].between(1, 5) & panel["hist_earn_move"].notna()
        bump = np.sqrt(panel["ivw_prev"] ** 2 + (panel["hist_earn_move"] * 0.8) ** 2 * 365 / 7)
        panel["ivw_prev"] = panel["ivw_prev"].where(~pre, bump)
    panel = add_ticker_traits(panel)
    f64 = panel.select_dtypes("float64").columns
    panel[f64] = panel[f64].astype(np.float32)            # halves memory on the real universe
    panel.attrs["iv_source"] = iv_source
    return panel


# (input feature, prior strength). Prior strength = how many "units of evidence" the market-wide
# estimate is worth. A ticker needs that much of its own history to move halfway to its own value.
MACRO_KEEP = ["world_politics_risk", "fred_epu_z", "fred_emv_epu_z", "politics_gpr_z", "politics_gpr_threats_z",
              "world_economy_attn", "world_politics_attn", "world_economy_tone", "world_politics_tone",
              "fred_dgs10_chg5", "fred_curve_10_2", "fred_hy_spread", "fred_hy_spread_chg5", "fred_breakeven10_chg5",
              "mkt_vix", "mkt_vix_chg", "mkt_spx_ret1", "mkt_spx_ret5", "mkt_dxy_ret1", "mkt_oil_ret1", "mkt_gold_ret1"]

TRAITS = {
    "news_on_wsent": 30.0,     # how much the stock keeps drifting after overnight news
    "gap_pre_z": 150.0,        # does it extend (+) or fade (-) its opening gap
    "ret_1d_z": 150.0,         # day-over-day continuation (+) or reversal (-)
    "eps_surprise": 0.5,       # post-earnings drift per unit of EPS surprise
    "peer_ind_earn_surp": 2.0, # how much it moves with its competitors' earnings surprises
    "peer_ind_ret1": 300.0,    # follows (+) or diverges from (-) its industry's moves
    "fred_dgs10_chg5": 300.0,  # its own sensitivity to rate moves
    "world_politics_risk": 300.0,  # ... and to political-world risk (policy uncertainty, geopolitics)
}


def add_ticker_traits(panel: pd.DataFrame) -> pd.DataFrame:
    """Per-ticker behaviour, estimated point-in-time with shrinkage to the market-wide value.

    trait_i(t) = (sum_{s<t} x_is*z_is + lam*b_mkt(t)) / (sum_{s<t} x_is^2 + lam)

    i.e. a ridge-shrunk univariate regression slope of the ticker's own open-to-close return
    (in vol units) on the feature, using only days before t. Interaction feature
    tx_<f> = trait * f lets the model apply each ticker's own sensitivity.
    """
    z = panel["z_oc"]
    known = z.notna()
    Y = z.fillna(0.0)
    tick = panel.index.get_level_values("ticker")
    day = panel.index.get_level_values("date")
    for f, lam in TRAITS.items():
        if f not in panel:
            continue
        X = panel[f].fillna(0.0) * known
        xy, xx = X * Y, X * X
        cxy = xy.groupby(tick).cumsum() - xy           # strictly before today
        cxx = xx.groupby(tick).cumsum() - xx
        gxy = xy.groupby(day).sum().cumsum().shift(1)
        gxx = xx.groupby(day).sum().cumsum().shift(1)
        bm = (gxy / gxx.replace(0, np.nan)).reindex(day).fillna(0.0).values
        # cumsum minus today = outcomes through t-1 close, all known at the t morning scan
        tr = (cxy + lam * bm) / (cxx + lam)
        panel[f"tr_{f}"] = tr.values
        panel[f"tx_{f}"] = (tr * panel[f].fillna(0.0)).values
        panel[f"n_{f}"] = cxx.values                    # evidence behind the trait
    return panel


TARGET_COLS = {"r_oc", "r_cc", "r_gap", "r_fwd5", "r_fwd20", "open_px", "close_px", "prev_close",
               "high_px", "low_px", "y_dir", "sig_d", "ivw_t", "z_oc", "z_x"}

# Interpretable linear core. tx_* terms carry each ticker's own sensitivities.
LINEAR_FEATURES = ["peer_ind_news", "peer_comp_news", "peer_corr_news", "peer_ind_earn_surp", "peer_comp_earn_surp", "peer_corr_earn_surp", "peer_ind_earn_react", "supply_news", "tx_peer_ind_earn_surp", "news_on_wsent", "sent_gap_divergence", "gap_pre_z", "ret_1d_z", "eps_surprise", "ev_earnings", "ev_guidance", "ev_analyst", "news_prior_wsent", "news_5d_wsent", "tx_news_on_wsent", "tx_gap_pre_z", "tx_ret_1d_z", "tx_eps_surprise", "mkt_gap", "sector_gap_rel"]


# Kept in the panel (shown as context in the UI) but not fed to the stock model. The holdout ablation
# (stockgpt/ablation.py, 2024-01 -> 2026-11) showed day-level macro/political features and peers' price
# moves lowered out-of-sample IC of the stock-relative model; they predict the market, not the stock.
STOCK_MODEL_EXCLUDE = ("fred_", "politics_", "world_", "mkt_vix", "mkt_spx", "mkt_dxy", "mkt_oil", "mkt_gold",
                       "sector_etf_ret1", "tr_fred", "tx_fred", "tr_world", "tx_world",
                       "peer_ind_ret1", "peer_corr_ret1", "peer_comp_ret1", "supply_ret1",
                       "tr_peer_ind_ret1", "tx_peer_ind_ret1")


def feature_columns(panel: pd.DataFrame, include_context: bool = False) -> list[str]:
    cols = [c for c in panel.columns if c not in TARGET_COLS and not c.startswith(("_", "n_"))]
    if not include_context:
        cols = [c for c in cols if not c.startswith(STOCK_MODEL_EXCLUDE)]
    return cols
