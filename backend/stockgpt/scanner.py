"""Morning scan: for the scan date, rank the liquid universe and build concrete trade tickets."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .backtest import _select, _select_stock, build_signals
from .config import SETTINGS
from .features import ET, _assign_sessions
from .models.ticker_model import GBM_WEIGHT, HierarchicalModel
from .options import bs_price, straddle_return

LABELS = {
    "news_on_wsent": "Overnight news sentiment", "sent_gap_divergence": "News vs. premarket gap",
    "gap_pre_z": "Premarket gap", "ret_1d_z": "Yesterday's move", "eps_surprise": "EPS surprise",
    "ev_earnings": "Earnings headlines", "ev_guidance": "Guidance headlines",
    "ev_analyst": "Analyst action", "news_prior_wsent": "Yesterday's session news",
    "news_5d_wsent": "5-day news trend", "tx_news_on_wsent": "This ticker's news drift",
    "tx_gap_pre_z": "This ticker's gap behaviour", "tx_ret_1d_z": "This ticker's reversal habit",
    "tx_eps_surprise": "This ticker's earnings drift", "mkt_gap": "Market premarket",
    "sector_gap_rel": "Gap vs. sector",
}


# Plain-language names for model inputs, shown as the reasons behind an idea.
FEATURE_LABELS = {
    "mkt_ret_1d": "Yesterday's market move", "mkt_gap": "Market premarket move", "gap_pre": "Premarket gap",
    "gap_pre_z": "Premarket gap", "sector_gap_rel": "Gap vs. its sector", "ret_1d_z": "Yesterday's move",
    "ret_5d_z": "Past week's move", "ret_20d_z": "Past month's move", "ret_60d_z": "Past quarter's move",
    "oc_autocorr_prev": "Yesterday's intraday move", "rel_volume_1d": "Unusual volume yesterday",
    "atr14_pct": "Recent trading range", "rv20": "Recent volatility", "iv30": "Implied volatility",
    "iv_rank": "Implied volatility vs. its own year", "iv_rv": "Implied vs. realised volatility",
    "ivw_prev": "Front-week implied volatility", "event_premium": "Event premium in options",
    "atm_spread": "Option spread", "log_adv20": "Trading liquidity", "log_mcap": "Company size",
    "dist_20d_high": "Distance from 20-day high", "dist_20d_low": "Distance from 20-day low",
    "dist_52w_high": "Distance from 52-week high", "days_to_earn": "Days until earnings",
    "days_since_earn": "Days since earnings", "is_earnings": "Earnings reaction day",
    "eps_surprise": "Earnings surprise", "hist_earn_move": "Typical earnings move",
    "tr_eps_surprise": "How this stock drifts after earnings", "tx_eps_surprise": "This stock's earnings drift",
    "intra_5": "Intraday trend, past week", "intra_20": "Intraday trend, past month",
    "intra_60": "Intraday trend, past quarter", "over_5": "Overnight trend, past week",
    "over_20": "Overnight trend, past month", "over_60": "Overnight trend, past quarter",
    "mom_12_1": "12-month momentum", "mom_6_1": "6-month momentum", "rev_1m": "One-month reversal",
    "max_20": "Biggest up day this month", "min_20": "Biggest down day this month",
    "amihud_20": "Price impact of trading", "turn_5_60": "Volume trend", "vol_5_60": "Volatility trend",
    "skew_60": "Return skew", "corr_pv_20": "Price-volume relationship", "rsv_20": "Position in 20-day range",
    "klen_5": "Recent daily ranges", "beta_60": "Market sensitivity", "ivol_60": "Stock-specific volatility",
    "news_on_wsent": "Overnight news sentiment", "news_on_count": "Overnight news volume",
    "news_abnormal": "Unusual news attention", "news_5d_wsent": "News sentiment this week",
    "news_prior_wsent": "Yesterday's session news", "sent_gap_divergence": "News vs. premarket gap",
    "peer_ind_news": "Competitors' overnight news", "peer_comp_news": "Named competitors' news",
    "peer_corr_news": "Related stocks' news", "peer_ind_earn_surp": "Competitors' earnings surprises",
    "peer_corr_earn_surp": "Related stocks' earnings surprises", "peer_ind_earn_react": "Competitors' earnings reactions",
    "peer_corr_earn_react": "Related stocks' earnings reactions", "peer_ind_events": "Competitor events overnight",
    "k8_results": "Company reported results (SEC filing)",
    "k8_update": "Company update filed with the SEC",
    "k8_other": "Other SEC filing",
    "k8_exec": "Executive or board change filed",
    "k8_agreement": "New contract or deal filed",
    "k8_bad": "Bad-news filing (write-down, delisting, restatement)",
    "k8_capital": "Share sale or new debt filed",
    "k8_any": "A company filing since the last close",
    "k8_n5": "Filings in the past week",
    "k8_n20": "Filings in the past month",
    "k8_since": "Days since the last company filing",
    "supply_news": "Customer and supplier news", "tr_gap_pre_z": "How this stock treats its gaps",
    "tr_ret_1d_z": "This stock's day-to-day habit", "tr_news_on_wsent": "How this stock drifts after news",
}


def _label(f: str) -> str:
    if f in FEATURE_LABELS:
        return FEATURE_LABELS[f]
    if f.startswith("f_"):
        return f[2:].replace("_", " ").replace("yoy", "(yoy)").capitalize()
    return f.replace("_", " ").capitalize()


def reasons(model: HierarchicalModel, rows: pd.DataFrame, top: int = 4) -> list[list[dict]]:
    """Per-row drivers of the tree model's prediction (LightGBM SHAP contributions, averaged over seeds).
    push > 0 moves the prediction toward 'beats the market', < 0 toward 'lags the market'."""
    X = rows[model.features].astype(np.float32)
    models = getattr(model, "gbms", None) or [model.gbm]
    contrib = np.mean([m.predict(X, pred_contrib=True)[:, :-1] for m in models], axis=0)
    market_wide = np.array([f in ("mkt_ret_1d", "mkt_gap") for f in model.features])
    out = []
    for i in range(len(rows)):
        c = contrib[i]
        c = np.where(market_wide, 0.0, c)             # same for every stock that day: not a reason for this one
        idx = np.argsort(-np.abs(c))[:top]
        tot = np.abs(c).sum() + 1e-12
        out.append([dict(label=_label(model.features[j]), key=model.features[j], push=float(c[j]),
                         share=float(abs(c[j]) / tot)) for j in idx if abs(c[j]) > 0])
    return out


def next_friday(d: pd.Timestamp, min_days: int = 4) -> pd.Timestamp:
    t = d + pd.Timedelta(days=min_days)
    return t + pd.Timedelta(days=(4 - t.weekday()) % 7)


def explain(model: HierarchicalModel, row: pd.Series, top: int = 4) -> list[dict]:
    x = np.array([row.get(f, 0.0) if pd.notna(row.get(f, np.nan)) else 0.0 for f in model.linear_features])
    contrib = model.ridge.coef_ * (x - model.lin_mu) / model.lin_sd
    idx = np.argsort(-np.abs(contrib))[:top]
    return [dict(factor=LABELS.get(model.linear_features[i], model.linear_features[i]),
                 key=model.linear_features[i], value=float(x[i]), contribution=float(contrib[i]))
            for i in idx if abs(contrib[i]) > 1e-4]


def market_betas(prices: pd.DataFrame, scan_date, window: int = 60) -> pd.Series:
    """How much each stock has moved per 1% move in the average stock, over the last `window`
    sessions before the scan. (The model's own beta feature is a rank, not this number.)"""
    px = prices[prices["date"] < pd.Timestamp(scan_date)]
    px = px[px["date"] >= px["date"].max() - pd.Timedelta(days=int(window * 1.6))]
    r = px.pivot(index="date", columns="ticker", values="close").pct_change().iloc[1:].tail(window)
    m = r.mean(axis=1)
    return r.apply(lambda c: c.cov(m)) / m.var()


def run_scan(panel: pd.DataFrame, tables: dict, model: HierarchicalModel, scan_date=None,
             equity: float | None = None, top_n: int = 8) -> dict:
    equity = equity or SETTINGS.risk.starting_capital
    dates = panel.index.get_level_values("date").unique().sort_values()
    scan_date = pd.Timestamp(scan_date) if scan_date is not None else dates[-1]
    today = panel.xs(scan_date, level="date", drop_level=False)
    preds = model.predict(today)
    sig = build_signals(today, preds)
    meta = tables["tickers"].set_index("ticker")

    news = tables["news"]
    win = news[(news["published_at"] >= (scan_date - pd.Timedelta(days=4)).tz_localize(ET))
               & (news["published_at"] < (scan_date + pd.Timedelta(hours=9)).tz_localize(ET))]
    from .features import score_news
    win = _assign_sessions(score_news(win), dates)
    win = win[win["date"] == scan_date]

    def headlines(tk, n=4):
        h = win[win["ticker"] == tk].sort_values("magnitude", ascending=False).head(n)
        return [dict(time=r.published_at.tz_convert(ET).strftime("%a %H:%M"), headline=r.headline,
                     source=getattr(r, "source", ""), sentiment=float(r.sentiment), event=r.event)
                for r in h.itertuples()]

    expiry = next_friday(scan_date)
    dte = max(1, (expiry - scan_date).days)

    def ticket(tk, row, kind):
        S = float(row["open_px"]) if pd.notna(row["open_px"]) else float(row["prev_close"] * np.exp(row["gap_pre"] if pd.notna(row["gap_pre"]) else 0.0))
        known_outcome = pd.notna(row.get("close_px"))
        base = dict(
            ticker=tk, name=meta.at[tk, "name"], sector=meta.at[tk, "sector"], kind=kind,
            direction="UP" if row["side"] == "CALL" else "DOWN", side=row["side"],
            prob=float(row["conf"]), mu_z=float(row["mu"]), exp_move_pct=float(row["exp_move_pct"]),
            premarket_gap=float(row["gap_pre"]), ref_price=S, iv=float(row["iv_entry"]),
            earnings_today=bool(row["is_earnings"] == 1), days_to_earn=int(row["days_to_earn"]),
            why=explain(model, row), headlines=headlines(tk),
            ticker_model=model.ticker_model(row), liquidity=dict(
                adv=float(10 ** row["log_adv20"]), opt_vol=float(10 ** row.get("log_opt_vol20", 0) - 1),
                spread=float(row.get("atm_spread", np.nan))),
        )
        if kind == "option":
            strike = float(row["strike"])
            prem = float(row["opt_paid"])
            frac = min(SETTINGS.risk.max_risk_per_trade, SETTINGS.risk.kelly_fraction * float(row["kelly"]))
            contracts = int(max(1, np.floor(equity * frac / (prem * 100)))) if prem > 0 else 0
            base.update(contract=f"{tk} {expiry:%d %b} {strike:g} {row['side'][0]}", strike=strike,
                        expiry=str(expiry.date()), premium=round(prem, 2), contracts=contracts,
                        max_loss=round(contracts * prem * 100, 2), ev=float(row["ev"]),
                        p_profit=float(row["p_profit"]), risk_frac=frac,
                        plan="Buy at the open, close by 15:55 ET. Stop if premium -50%.")
            if known_outcome:
                base["outcome"] = dict(stock_move=float(row["r_oc"]), option_ret=float(row["opt_ret"]),
                                       correct=bool(row["hit"]))
        else:
            notional = equity * SETTINGS.risk.stock_leverage / SETTINGS.risk.max_positions
            base.update(shares=int(notional / S), notional=round(notional, 2), exp_ret=float(row["exp_ret"]),
                        plan="Enter at the open, exit at the close. Stop at 1x expected move against.")
            if known_outcome:
                base["outcome"] = dict(stock_move=float(row["r_oc"]), stock_ret=float(row["stock_ret"]),
                                       correct=bool(row["hit"]))
        return base

    r_ = SETTINGS.risk
    cost = 2 * SETTINGS.costs.stock_slippage_bps / 1e4
    sig["exp_ret"] = (2 * sig["conf"] - 1) * sig["exp_move_pct"] - cost
    liq = sig[sig["liquid"]]
    opt_q = set(_select(sig).index)
    stk_q = set(_select_stock(sig, r_.max_positions).index)
    opt = liq.nlargest(top_n, "ev")
    stk = liq.nlargest(top_n, "exp_ret")
    # ---- ranked ideas: what the model would buy and short today (it ranks stocks against the market) ----
    from .backtest import pick_sides
    liq = liq.assign(score_pct=liq["mu"].rank(pct=True))
    notional = equity * r_.stock_leverage / (2 * r_.rank_k)

    def ideas(part: pd.DataFrame, action: str) -> list[dict]:
        if part.empty:
            return []
        why = reasons(model, today.loc[part.index])
        out = []
        for ((d, tk), r), w in zip(part.iterrows(), why):
            buy = action == "BUY"
            S = float(r["open_px"]) if pd.notna(r["open_px"]) else float(r["prev_close"] * np.exp(r["gap_pre"] if pd.notna(r["gap_pre"]) else 0.0))
            pct_ = float(r["score_pct"])
            edge = pct_ if buy else 1 - pct_
            strength = "Strong" if edge >= 0.99 else "Solid" if edge >= 0.97 else "Moderate"
            sgn = 1 if buy else -1
            item = dict(
                ticker=tk, name=meta.at[tk, "name"], sector=meta.at[tk, "sector"], action=action,
                strength=strength, score=float(r["mu"]), score_pct=pct_,
                exp_move_pct=float(r["exp_move_pct"]), premarket_gap=float(r["gap_pre"]) if pd.notna(r["gap_pre"]) else None,
                ref_price=S, earnings_today=bool(r["is_earnings"] == 1), days_to_earn=int(r["days_to_earn"]),
                shares=int(notional / S) if S > 0 else 0, notional=round(notional, 2),
                why=[dict(label=x["label"], key=x["key"], helps=bool(x["push"] * sgn > 0), share=x["share"]) for x in w],
                headlines=headlines(tk, 3),
                plan=("Buy at the open, sell at the close." if buy else "Short at the open, cover at the close."))
            # the option that expresses the same view, and whether it clears costs
            call = buy
            if pd.notna(r.get("opt_paid")) and (r["side"] == ("CALL" if call else "PUT")):
                prem = float(r["opt_paid"])
                item["option"] = dict(contract=f"{tk} {expiry:%d %b} {float(r['strike']):g} {'C' if call else 'P'}",
                                      premium=round(prem, 2), ev=float(r["ev_cal"]), p_profit=float(r["p_profit"]),
                                      worth_it=bool(r["ev_cal"] > r_.min_ev),
                                      contracts=int(max(1, np.floor(equity * r_.max_risk_per_trade / (prem * 100)))) if prem > 0 else 0)
            if pd.notna(r.get("close_px")):
                rel = float(r["r_oc"] - liq["r_oc"].mean())
                item["outcome"] = dict(stock_move=float(r["r_oc"]), rel_move=rel, correct=bool(rel * sgn > 0),
                                       trade_ret=float(sgn * r["r_oc"]))
            out.append(item)
        return out

    n_ideas = 10
    top, bottom = pick_sides(liq, n_ideas)             # shown ideas: the strongest of each beta group, both sides
    longs, shorts = ideas(top, "BUY"), ideas(bottom, "SHORT")
    betas = market_betas(tables["prices"], scan_date)
    def _beta(part):
        v = betas.reindex(part.index.get_level_values("ticker")).mean() if len(part) else np.nan
        return round(float(v), 2) if pd.notna(v) else None
    for part, items in ((top, longs), (bottom, shorts)):
        for tk, item in zip(part.index.get_level_values("ticker"), items):
            item["beta"] = round(float(betas[tk]), 2) if tk in betas.index and pd.notna(betas[tk]) else None

    option_tickets = [dict(ticket(tk, r, "option"), qualifies=(d, tk) in opt_q) for (d, tk), r in opt.iterrows()]
    stock_tickets = [dict(ticket(tk, r, "stock"), qualifies=(d, tk) in stk_q) for (d, tk), r in stk.iterrows()]

    # Earnings tonight / tomorrow morning: straddle candidates priced at today's close
    upcoming = sig[(sig["days_to_earn"] == 1) & sig["liquid"]].copy()
    straddles = []
    for (_, tk), r in upcoming.iterrows():
        S = float(r["close_px"] if pd.notna(r["close_px"]) else r["prev_close"])
        ivw = float(r["ivw_t"] if pd.notna(r.get("ivw_t")) else r["ivw_prev"])
        K = float(np.round(S))
        mid = bs_price(S, K, 7 / 365, ivw, SETTINGS.options.risk_free_rate, True) + \
            bs_price(S, K, 7 / 365, ivw, SETTINGS.options.risk_free_rate, False)
        implied = float(mid / S)
        hist = float(r["hist_earn_move"]) if pd.notna(r["hist_earn_move"]) else None
        edge = (hist / implied) if hist else None
        straddles.append(dict(ticker=tk, name=meta.at[tk, "name"], sector=meta.at[tk, "sector"],
                              implied_move=implied, hist_move=hist, edge_ratio=edge,
                              signal="BUY STRADDLE" if edge and edge >= SETTINGS.options.straddle_edge_ratio
                              else ("SELL PREMIUM (defined risk)" if edge and edge < 0.8 else "PASS"),
                              iv=ivw, premium=float(mid), headlines=headlines(tk, 2)))
    straddles.sort(key=lambda d: -(d["edge_ratio"] or 0))

    liquid = sig[sig["liquid"]]
    heat = liquid.assign(score=(liquid["conf"] - 0.5) * np.where(liquid["side"] == "CALL", 1, -1))
    sectors = (heat.join(meta["sector"], on="ticker").groupby("sector")
               .agg(score=("score", "mean"), news=("news_on_count", "sum"), n=("score", "size"))
               .reset_index().to_dict("records"))
    movers = (liquid.assign(abs_gap=liquid["gap_pre_z"].abs()).nlargest(12, "abs_gap"))
    movers = [dict(ticker=tk, name=meta.at[tk, "name"], gap=float(r["gap_pre"]), gap_z=float(r["gap_pre_z"]),
                   news=int(r["news_on_count"]), sentiment=float(r["news_on_wsent"]), prob_up=float(r["p_cal"]))
              for (_, tk), r in movers.iterrows()]
    from .config import DATA_DIR
    mp = DATA_DIR / "macro_raw.parquet"
    first = {}
    if mp.exists():
        from .sources.macro import daily_features
        first = daily_features(pd.read_parquet(mp), pd.DatetimeIndex([scan_date])).iloc[0].to_dict()
    def g(c):
        v = first.get(c, np.nan)
        return float(v) if pd.notna(v) else None
    world = dict(politics_risk=g("world_politics_risk"), policy_uncertainty_z=g("fred_epu_z"),
                 geopolitical_risk_z=g("politics_gpr_z"), geopolitical_threats_z=g("politics_gpr_threats_z"),
                 vix=g("mkt_vix"), vix_chg=g("mkt_vix_chg"), curve_10_2=g("fred_curve_10_2"),
                 hy_spread=g("fred_hy_spread"), hy_spread_chg5=g("fred_hy_spread_chg5"),
                 dgs10_chg5=g("fred_dgs10_chg5"), spx_ret1=g("mkt_spx_ret1"), dxy_ret1=g("mkt_dxy_ret1"),
                 oil_ret1=g("mkt_oil_ret1"), gold_ret1=g("mkt_gold_ret1"))
    return dict(
        world=world,
        scan_date=str(scan_date.date()), generated_for="09:00 ET premarket",
        option_expiry=str(expiry.date()), universe=sorted(liquid.index.get_level_values("ticker")),
        equity=equity, universe_size=int(liquid.shape[0]), scanned=int(sig.shape[0]),
        market=dict(premarket_gap=float(today["mkt_gap"].iloc[0]), news_items=int(today["news_on_count"].sum()),
                    regime_vol=float(today["rv20"].median()), cal_slope=model.cal_slope),
        longs=longs, shorts=shorts, book=dict(per_position=round(notional, 2), positions=2 * r_.rank_k,
                                               beta_long=_beta(top), beta_short=_beta(bottom), beta_neutral=r_.beta_neutral),
        options=option_tickets, stocks=stock_tickets, straddles=straddles[:12],
        thresholds=dict(min_prob=r_.min_edge_prob, min_ev=r_.min_ev),
        qualified=dict(options=len(opt_q), stocks=len(stk_q)),
        sectors=sectors, movers=movers,
    )
