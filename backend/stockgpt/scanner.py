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


def next_friday(d: pd.Timestamp, min_days: int = 4) -> pd.Timestamp:
    t = d + pd.Timedelta(days=min_days)
    return t + pd.Timedelta(days=(4 - t.weekday()) % 7)


def explain(model: HierarchicalModel, row: pd.Series, top: int = 4) -> list[dict]:
    x = np.array([row.get(f, 0.0) if pd.notna(row.get(f, np.nan)) else 0.0 for f in model.linear_features])
    contrib = (1 - GBM_WEIGHT) * model.ridge.coef_ * (x - model.lin_mu) / model.lin_sd
    idx = np.argsort(-np.abs(contrib))[:top]
    return [dict(factor=LABELS.get(model.linear_features[i], model.linear_features[i]),
                 key=model.linear_features[i], value=float(x[i]), contribution=float(contrib[i]))
            for i in idx if abs(contrib[i]) > 1e-4]


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
        S = float(row["open_px"]) if pd.notna(row["open_px"]) else float(row["prev_close"] * np.exp(row["gap_pre"] or 0))
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
    return dict(
        scan_date=str(scan_date.date()), generated_for="09:00 ET premarket",
        equity=equity, universe_size=int(liquid.shape[0]), scanned=int(sig.shape[0]),
        market=dict(premarket_gap=float(today["mkt_gap"].iloc[0]), news_items=int(today["news_on_count"].sum()),
                    regime_vol=float(today["rv20"].median()), cal_slope=model.cal_slope),
        options=option_tickets, stocks=stock_tickets, straddles=straddles[:12],
        thresholds=dict(min_prob=r_.min_edge_prob, min_ev=r_.min_ev),
        qualified=dict(options=len(opt_q), stocks=len(stk_q)),
        sectors=sectors, movers=movers,
    )
