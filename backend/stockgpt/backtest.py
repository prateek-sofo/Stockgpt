"""Walk-forward backtest.

1. Expanding-window training, refit every `retrain_every_days`, with an embargo gap. Each day
   is predicted only by a model trained strictly on earlier days.
2. Three strategies are simulated from the out-of-sample predictions:
   * options_daytrade: buy ATM weekly call/put at the open, sell at the close.
   * stock_daytrade:   same picks, shares, 2x intraday leverage.
   * earnings_straddle: buy a straddle the close before earnings when the ticker's own
     history of earnings moves exceeds what options imply; sell at the reaction-day close.
3. Costs: bid-ask (crossing half the spread each side), commissions, stock slippage.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import SETTINGS
from .features import feature_columns
from .models.ticker_model import HierarchicalModel
from .options import (day_trade_return, expected_day_trade, kelly_fraction,
                      straddle_return)
from .universe import liquid_mask


# ---------------------------------------------------------------------------- walk forward
def walk_forward(panel: pd.DataFrame, features: list[str] | None = None, progress=None):
    cfg = SETTINGS.model
    features = features or feature_columns(panel)
    dates = panel.index.get_level_values("date").unique().sort_values()
    preds, models = [], []
    starts = list(range(cfg.min_train_days, len(dates), cfg.retrain_every_days))
    for n, b in enumerate(starts):
        train_end = dates[b - cfg.embargo_days]
        test_dates = dates[b:b + cfg.retrain_every_days]
        tr = panel[panel.index.get_level_values("date") < train_end]
        te = panel[panel.index.get_level_values("date").isin(test_dates)]
        model = HierarchicalModel(features).fit(tr)
        # calibrate on all *earlier* out-of-sample predictions (never on this block)
        if preds:
            tgt = SETTINGS.model.target if SETTINGS.model.target in panel else "z_oc"
            hist = pd.concat(preds).join(panel[tgt])
            model.calibrate(hist["mu"].values, hist[tgt].values)
        pr = model.predict(te)
        preds.append(pr)
        models.append((test_dates[0], model.cal_slope))
        last = model
        if progress:
            progress(n + 1, len(starts))
    return pd.concat(preds).sort_index(), {"cal_history": models, "last_model": last}


# ------------------------------------------------------------------------------- signals
def entry_exit_iv(df: pd.DataFrame):
    """IV used to buy at the open and sell at the close.

    We only observe end-of-day IV, so the open is modelled conservatively:
    * normal day: yesterday's front-week IV, marked up with the size of the premarket gap
      (a big gap means news, and market makers widen IV at the open);
    * earnings reaction day: the event has passed, so start from today's post-event IV
      plus 10%, with the same gap mark-up.
    Exit uses today's end-of-day IV.
    """
    ivw_prev = df["ivw_prev"].fillna(df["iv30"]).values
    ivw_t = df["ivw_t"].fillna(df["iv30"]).values if "ivw_t" in df else ivw_prev
    gz2 = np.minimum(df["gap_pre_z"].fillna(0).values ** 2, 16)
    bump = np.sqrt(1 + 0.15 * gz2)
    earn = df["is_earnings"].fillna(0).values == 1
    entry = np.where(earn, ivw_t * 1.10, np.maximum(ivw_prev, ivw_t)) * bump
    exit_ = ivw_t
    return np.nan_to_num(entry, nan=0.4), np.nan_to_num(exit_, nan=0.4)


def expected_iv_at_open(df: pd.DataFrame):
    """Entry / exit IV estimates using only information available at 09:00 ET.
    Used for trade *selection*. (entry_exit_iv is the simulated market used for P&L.)"""
    ivw_prev = df["ivw_prev"].fillna(df["iv30"]).values
    iv30 = df["iv30"].values
    gz2 = np.minimum(df["gap_pre_z"].fillna(0).values ** 2, 16)
    bump = np.sqrt(1 + 0.15 * gz2)
    earn = df["is_earnings"].fillna(0).values == 1
    # after earnings, front-week IV collapses toward the 30d level ex-event
    post = np.minimum(iv30, ivw_prev)
    entry = np.where(earn, post * 1.10, ivw_prev) * bump
    exit_ = np.where(earn, post * 0.95, ivw_prev)
    return np.nan_to_num(entry, nan=0.4), np.nan_to_num(exit_, nan=0.4)


# Columns the signal / simulation / reporting steps read. On the full universe, joining only these
# (not all ~150 feature columns) keeps the backtest within memory.
SIGNAL_COLS = ["open_px", "close_px", "prev_close", "high_px", "low_px", "r_oc", "r_cc", "r_fwd5", "r_fwd20", "z_oc", "z_x", "z5_x", "y_dir",
               "sig_d", "rv20", "is_earnings", "days_to_earn", "hist_earn_move", "eps_surprise", "iv30", "ivw_prev",
               "ivw_t", "gap_pre", "gap_pre_z", "atm_spread", "log_adv20", "log_opt_vol20", "news_on_count",
               "news_on_wsent", "mkt_gap", "beta_60"]


def build_signals(panel: pd.DataFrame, preds: pd.DataFrame, slim: bool = False) -> pd.DataFrame:
    if slim:
        panel = panel[[c for c in SIGNAL_COLS if c in panel]]
    df = panel.join(preds, how="inner")
    df["liquid"] = liquid_mask(df)
    S0 = df["open_px"].values
    iv_entry, iv_exit = entry_exit_iv(df)
    spread = df["atm_spread"].fillna(0.05).values
    exp_move = (df["exp_absz"] * df["sig_d"]).values
    est_in, est_out = expected_iv_at_open(df)
    e = expected_day_trade(S0, df["p_cal"].values, exp_move, est_in, spread, iv_exit=est_out)
    is_call = e["is_call"]
    df["side"] = np.where(is_call, "CALL", "PUT")
    df["conf"], df["ev"], df["win_ret"], df["loss_ret"] = e["conf"], e["ev"], e["win"], e["loss"]
    df["p_profit"], df["moneyness"] = e["p_profit"], e["moneyness"]
    df["kelly"] = kelly_fraction(e["p_profit"], e["win"], e["loss"])
    df["exp_move_pct"] = exp_move
    df["iv_entry"] = iv_entry
    # realised outcome (only used for scoring, never for selection)
    real, K, mid, paid = day_trade_return(S0, df["close_px"].values, iv_entry, iv_exit, is_call, spread,
                                          moneyness=df["moneyness"].values)
    df["opt_ret"], df["strike"], df["opt_mid"], df["opt_paid"] = real, K, mid, paid
    df["ev_cal"] = calibrate_ev(df)
    sign = np.where(is_call, 1, -1)
    df["stock_ret"] = sign * df["r_oc"] - 2 * SETTINGS.costs.stock_slippage_bps / 1e4
    df["hit"] = (np.sign(df["r_oc"]) == sign).astype(int)
    return df


def calibrate_ev(df: pd.DataFrame, min_days: int = 60) -> np.ndarray:
    """Correct the option EV forecast by its own track record (expanding, strictly prior days).

    ev_cal(t) = a(t) + b(t) * ev, with (a, b) the OLS fit of realised option return on
    forecast EV over selectable rows (liquid, conf >= min) on days before t. Picking the
    highest-EV names selects over-estimates (winner's curse); this removes that bias.
    """
    r = SETTINGS.risk
    sel = (df["liquid"] & (df["conf"] >= r.min_edge_prob) & df["opt_ret"].notna()).values
    x = np.where(sel, df["ev"].clip(-1, 1).values, 0.0)
    y = np.where(sel, df["opt_ret"].clip(-1, 3).values, 0.0)
    w = sel.astype(float)
    day = df.index.get_level_values("date")
    agg = pd.DataFrame({"n": w, "x": x, "y": y, "xx": x * x, "xy": x * y}).groupby(day).sum()
    cum = agg.cumsum().shift(1).fillna(0.0)
    days_seen = np.arange(len(cum))
    n, sx, sy, sxx, sxy = (cum[c].values for c in ("n", "x", "y", "xx", "xy"))
    var = sxx - sx * sx / np.maximum(n, 1)
    b = np.where(var > 1e-9, (sxy - sx * sy / np.maximum(n, 1)) / np.maximum(var, 1e-9), 0.0)
    b = np.clip(b, 0.0, 1.0)
    a = (sy - b * sx) / np.maximum(n, 1)
    ok = (days_seen >= min_days) & (n > 200)
    a = np.where(ok, a, -0.03)
    b = np.where(ok, b, 0.5)
    pos = cum.index.get_indexer(day)
    return a[pos] + b[pos] * df["ev"].clip(-1, 1).values


def rank_score(day: pd.DataFrame) -> pd.Series:
    """The score stocks are ranked on within one day. The raw prediction often lines up with how
    strongly a stock follows the market (its beta), which turns a long/short book into a hidden bet
    on the market's direction. Here the part of the score explained by beta is removed first, so the
    longs and the shorts carry similar market sensitivity. On 1,723 out-of-sample days this cut the
    worst drawdown from -44% to -35% with the same hit rate (docs/RESEARCH.md)."""
    mu = day["mu"]
    if not SETTINGS.risk.beta_neutral or "beta_60" not in day or len(day) < 20:
        return mu
    beta = day["beta_60"].astype(float)
    b = beta.fillna(beta.median()).rank(pct=True) - 0.5
    var = float(b.var())
    if not np.isfinite(var) or var <= 0:
        return mu
    slope = float(np.cov(mu.astype(float), b)[0, 1]) / var
    return mu - slope * b


def pick_sides(liq: pd.DataFrame, k: int, buckets: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The k stocks to buy and the k to short on one day. With beta_neutral on, the day's stocks are
    split into five groups by market sensitivity and the strongest and weakest k/5 are taken inside
    each group, so every buy has a short of similar beta and the two books move alike with the
    market. Removing beta from the score alone (rank_score) balances the books on average but can
    leave a large gap on a given day; matching inside groups bounds it every day."""
    if not SETTINGS.risk.beta_neutral or "beta_60" not in liq or len(liq) < 10 * buckets or k < buckets:
        liq = liq.assign(rank_mu=rank_score(liq))
        return liq.nlargest(k, "rank_mu"), liq.nsmallest(k, "rank_mu")
    beta = liq["beta_60"].astype(float)
    grp = pd.qcut(beta.fillna(beta.median()).rank(method="first"), buckets, labels=False)
    liq = liq.assign(rank_mu=liq["mu"] - liq["mu"].groupby(grp).transform("mean"))     # score relative to its beta group
    per = k // buckets
    longs = pd.concat([g.nlargest(per, "mu") for _, g in liq.groupby(grp)])
    shorts = pd.concat([g.nsmallest(per, "mu") for _, g in liq.groupby(grp)])
    return longs.sort_values("rank_mu", ascending=False), shorts.sort_values("rank_mu")


def _select_stock(day: pd.DataFrame, n: int):
    """Stock day trades: rank by expected return in bps after round-trip slippage."""
    cost = 2 * SETTINGS.costs.stock_slippage_bps / 1e4
    er = (2 * day["conf"] - 1) * day["exp_move_pct"] - cost
    if SETTINGS.risk.stock_selection == "rank":
        # ranking signal traded as a ranking: long the k strongest, short the k weakest liquid names
        k = max(1, n // 2) if n < 2 * SETTINGS.risk.rank_k else SETTINGS.risk.rank_k
        liq = day[day["liquid"]].assign(exp_ret=er)
        # relative ranking: top k long, bottom k short whatever the sign of mu (equal books = market-neutral)
        if len(liq) < 2 * k:
            return liq.iloc[:0]
        if liq["r_oc"].notna().any():              # a stock with no open or close that day cannot be traded or marked
            liq = liq[liq["r_oc"].notna()]
        longs, shorts = pick_sides(liq, k)
        longs, shorts = longs.assign(side="CALL"), shorts.assign(side="PUT")
        out = pd.concat([longs, shorts])
        sign = np.where(out["side"] == "CALL", 1, -1)
        out["stock_ret"] = sign * out["r_oc"] - 2 * SETTINGS.costs.stock_slippage_bps / 1e4
        out["hit"] = (np.sign(out["r_oc"]) == sign).astype(int)
        return out
    c = day[day["liquid"] & (day["conf"] >= SETTINGS.risk.min_edge_prob) & (er > 0)].assign(exp_ret=er)
    if not SETTINGS.risk.market_neutral:
        return c.nlargest(n, "exp_ret")
    # market-neutral book: the best half longs and the best half shorts (the model ranks stocks vs the market)
    k = max(1, n // 2)
    longs = c[c["side"] == "CALL"].nlargest(k, "exp_ret")
    shorts = c[c["side"] == "PUT"].nlargest(k, "exp_ret")
    m = min(len(longs), len(shorts))
    return pd.concat([longs.iloc[:m], shorts.iloc[:m]])


def _select(day: pd.DataFrame):
    r = SETTINGS.risk
    c = day[day["liquid"] & (day["conf"] >= r.min_edge_prob) & (day["ev_cal"] > r.min_ev)]
    return c.nlargest(r.max_positions, "ev_cal")


def simulate(sig: pd.DataFrame, straddles: bool = True) -> dict:
    """straddles=False when IV is estimated: a straddle's edge is (realised move - implied move), and
    an estimated implied move makes that number an artefact of the estimate, not a measurement."""
    r = SETTINGS.risk
    eq_o = eq_s = eq_e = eq_w = r.starting_capital
    swing_queue: list[float] = []
    curve, trades = [], []
    stress = 1.0
    for date, day in sig.groupby(level="date"):
        picks = _select(day)
        pnl_o = pnl_s = 0.0
        if len(picks):
            frac = np.minimum(r.max_risk_per_trade, r.kelly_fraction * picks["kelly"].values) * stress
            tot = frac.sum()
            if tot > r.max_daily_risk:
                frac *= r.max_daily_risk / tot
            pnl_o = float((eq_o * frac * picks["opt_ret"].clip(-1, None).values).sum())
            for (dt, tk), row, f in zip(picks.index, picks.itertuples(), frac):
                trades.append(dict(date=str(dt.date()), ticker=tk, side=row.side, strategy="options_daytrade",
                                   conf=round(row.conf, 4), ev=round(row.ev_cal, 4), ev_raw=round(row.ev, 4), strike=float(row.strike),
                                   entry=round(float(row.opt_paid), 3), ret=round(float(row.opt_ret), 4),
                                   risk_frac=round(float(f), 4), pnl=round(float(eq_o * f * max(row.opt_ret, -1)), 2),
                                   stock_move=round(float(row.r_oc), 4), hit=int(row.hit),
                                   earnings=int(row.is_earnings), moneyness=float(row.moneyness)))
        sp = _select_stock(day, 2 * r.rank_k if r.stock_selection == "rank" else r.max_positions)
        if len(sp):
            # equal weight across the long and short books (inverse-vol sizing was tested and did worse:
            # it concentrates on calm names where fixed costs exceed the edge)
            notional_v = np.full(len(sp), r.stock_leverage / max(len(sp), 1 if r.stock_selection == "rank" else r.max_positions))
            notional = float(notional_v.mean())
            pnl_s = float((eq_s * notional_v * sp["stock_ret"].values).sum())
            for (dt, tk), row, nv in zip(sp.index, sp.itertuples(), notional_v):
                notional = float(nv)
                trades.append(dict(date=str(dt.date()), ticker=tk, side="LONG" if row.side == "CALL" else "SHORT",
                                   strategy="stock_daytrade", conf=round(row.conf, 4), ev=round(float(row.exp_ret), 5),
                                   strike=None, entry=round(float(row.open_px), 2), ret=round(float(row.stock_ret), 5),
                                   risk_frac=round(notional, 4), pnl=round(float(eq_s * notional * row.stock_ret), 2),
                                   stock_move=round(float(row.r_oc), 4), hit=int(row.hit), earnings=int(row.is_earnings)))
        # earnings straddles (entered at prior close, priced at prior-day IV)
        er = day[(day["is_earnings"] == 1) & day["liquid"] & day["hist_earn_move"].notna()] if straddles else day.iloc[:0]
        pnl_e = 0.0
        if len(er):
            S0, S1 = er["prev_close"].values, er["close_px"].values
            ret, K, mid, paid = straddle_return(S0, S1, er["ivw_prev"].fillna(er["iv30"]).values,
                                                er["ivw_t"].fillna(er["iv30"]).values,
                                                er["atm_spread"].fillna(0.05).values)
            implied = mid / S0
            take = er["hist_earn_move"].values > implied * SETTINGS.options.straddle_edge_ratio
            for (dt, tk), rr, ok, im, hm, pd_ in zip(er.index, ret, take, implied, er["hist_earn_move"].values, paid):
                if not ok:
                    continue
                f = 0.01
                pnl_e += eq_e * f * max(rr, -1)
                trades.append(dict(date=str(dt.date()), ticker=tk, side="STRADDLE", strategy="earnings_straddle",
                                   conf=None, ev=round(float(hm / im - 1), 4), strike=None,
                                   entry=round(float(pd_), 3), ret=round(float(rr), 4), risk_frac=f,
                                   pnl=round(float(eq_e * f * max(rr, -1)), 2),
                                   stock_move=None, hit=int(rr > 0), earnings=1,
                                   implied_move=round(float(im), 4), hist_move=round(float(hm), 4)))
        # 5-session swing book: a new sleeve each day (top-k long / bottom-k short by the 5-day model),
        # each sleeve gets 1/5 of capital at 1x long + 1x short, booked when it closes (4 sessions later)
        if "mu5" in day:
            liq5 = day[day["liquid"] & day["mu5"].notna() & day["r_fwd5"].notna()]
            k5 = r.swing_k
            if len(liq5) >= 4 * k5:
                lg, sh = liq5.nlargest(k5, "mu5"), liq5.nsmallest(k5, "mu5")
                cost = 2 * SETTINGS.costs.stock_slippage_bps / 1e4
                sleeve = (np.expm1(lg["r_fwd5"]).mean() - cost) - (np.expm1(sh["r_fwd5"]).mean() + cost)
                swing_queue.append(sleeve / 5.0)
                for side, grp in (("LONG", lg), ("SHORT", sh)):
                    for (dt, tk), row in zip(grp.index, grp.itertuples()):
                        sgn = 1 if side == "LONG" else -1
                        rr = sgn * float(np.expm1(row.r_fwd5)) - cost
                        trades.append(dict(date=str(dt.date()), ticker=tk, side=side, strategy="swing_5d", conf=None,
                                           ev=round(float(row.mu5), 4), strike=None, entry=round(float(row.open_px), 2),
                                           ret=round(rr, 5), risk_frac=round(1 / (5 * k5), 4),
                                           pnl=round(eq_w * rr / (5 * k5), 2), stock_move=round(float(row.r_fwd5), 4),
                                           hit=int(rr > 0), earnings=int(row.is_earnings)))
            else:
                swing_queue.append(0.0)
            if len(swing_queue) > 4:
                eq_w *= 1 + swing_queue.pop(0)
        day_ret_o = pnl_o / eq_o
        eq_o += pnl_o
        eq_s += pnl_s
        eq_e += pnl_e
        stress = 0.5 if day_ret_o < -r.daily_loss_stop else 1.0
        curve.append(dict(date=str(date.date()), options=round(eq_o, 2), stock=round(eq_s, 2),
                          straddle=round(eq_e, 2), swing=round(eq_w, 2), n=len(picks)))
    curve = pd.DataFrame(curve)
    return {"curve": curve, "trades": pd.DataFrame(trades)}


# ------------------------------------------------------------------------------- metrics
def stats(equity: pd.Series, trades: pd.DataFrame | None = None) -> dict:
    eq = equity.astype(float).values
    rets = np.diff(eq) / eq[:-1]
    n = len(rets)
    yrs = max(n / 252, 1e-9)
    total = eq[-1] / eq[0] - 1
    peak = np.maximum.accumulate(eq)
    dd = eq / peak - 1
    sd = rets.std()
    downside = rets[rets < 0].std() if (rets < 0).any() else np.nan
    out = dict(start=float(eq[0]), end=float(eq[-1]), total_return=float(total),
               cagr=float((eq[-1] / eq[0]) ** (1 / yrs) - 1) if eq[-1] > 0 else -1.0,
               sharpe=float(rets.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0,
               sortino=float(rets.mean() / downside * np.sqrt(252)) if downside and downside > 0 else 0.0,
               max_drawdown=float(dd.min()), vol_annual=float(sd * np.sqrt(252)),
               best_day=float(rets.max()) if n else 0.0, worst_day=float(rets.min()) if n else 0.0,
               days=int(n))
    if trades is not None and len(trades):
        w = trades[trades["ret"] > 0]["ret"]
        l = trades[trades["ret"] <= 0]["ret"]
        out.update(trades=int(len(trades)), win_rate=float((trades["ret"] > 0).mean()),
                   avg_win=float(w.mean()) if len(w) else 0.0, avg_loss=float(l.mean()) if len(l) else 0.0,
                   profit_factor=float(trades.loc[trades.pnl > 0, "pnl"].sum() /
                                       max(1e-9, -trades.loc[trades.pnl <= 0, "pnl"].sum())))
    return out


def deflated_sharpe(equity: pd.Series, trials: int) -> dict:
    """Bailey & López de Prado (2014). Probability that the true Sharpe is above zero after allowing for
    having picked the best of `trials` strategy variants, and for skewed / fat-tailed returns."""
    from scipy.stats import kurtosis, norm, skew
    eq = equity.astype(float).values
    r = np.diff(eq) / eq[:-1]
    r = r[np.isfinite(r)]
    T = len(r)
    if T < 60 or r.std() == 0:
        return dict(sharpe=0.0, psr=None, dsr=None, trials=trials)
    sr = r.mean() / r.std()                                     # per-period
    g3, g4 = skew(r), kurtosis(r, fisher=False)
    se = np.sqrt((1 - g3 * sr + (g4 - 1) / 4 * sr ** 2) / (T - 1))
    emc = 0.5772156649
    n = max(trials, 2)
    sr0 = se * ((1 - emc) * norm.ppf(1 - 1 / n) + emc * norm.ppf(1 - 1 / (n * np.e)))   # expected best of n under no skill
    return dict(sharpe=float(sr * np.sqrt(252)), psr=float(norm.cdf(sr / se)), dsr=float(norm.cdf((sr - sr0) / se)),
                sharpe_needed=float(sr0 * np.sqrt(252)), trials=int(trials), skew=float(g3), kurtosis=float(g4))


def calibration(sig: pd.DataFrame, bins: int = 10) -> list[dict]:
    s = sig[sig["liquid"]]
    q = pd.cut(s["p_cal"], np.linspace(s["p_cal"].quantile(0.005), s["p_cal"].quantile(0.995), bins + 1))
    g = s.groupby(q, observed=True).agg(pred=("p_cal", "mean"), actual=("y_dir", "mean"), n=("y_dir", "size"),
                                        ret=("r_oc", "mean"))
    return [dict(pred=float(r.pred), actual=float(r.actual), n=int(r.n), ret=float(r.ret)) for r in g.itertuples()]


def deciles(sig: pd.DataFrame) -> list[dict]:
    s = sig[sig["liquid"]].copy()
    s["signal"] = (s["p_cal"] - 0.5) * s["exp_absz"]
    s["dec"] = s.groupby(level="date")["signal"].transform(
        lambda x: pd.qcut(x.rank(method="first"), 10, labels=False) if len(x) >= 10 else np.nan)
    g = s.groupby("dec").agg(ret=("r_oc", "mean"), hit=("y_dir", "mean"), n=("r_oc", "size"))
    return [dict(decile=int(d) + 1, ret_bps=float(r.ret * 1e4), up_rate=float(r.hit), n=int(r.n))
            for d, r in g.iterrows()]


def information_coefficient(sig: pd.DataFrame) -> dict:
    s = sig[sig["liquid"]]
    sc = (s["p_cal"] - 0.5)
    ic = pd.concat([sc, s["r_oc"]], axis=1).groupby(level="date").apply(
        lambda d: d.iloc[:, 0].corr(d.iloc[:, 1], method="spearman"))
    hit = s["hit"].mean()
    return dict(ic_mean=float(ic.mean()), ic_ir=float(ic.mean() / ic.std() * np.sqrt(252)),
                hit_rate_all=float(hit),
                hit_rate_conf60=float(s.loc[s["conf"] >= 0.6, "hit"].mean()) if (s["conf"] >= 0.6).any() else None,
                auc=_auc(s["y_dir"].values, s["p_cal"].values))


def _auc(y, p):
    from sklearn.metrics import roc_auc_score
    try:
        return float(roc_auc_score(y, p))
    except ValueError:
        return None


def monthly_returns(curve: pd.DataFrame, col: str) -> list[dict]:
    c = curve.set_index(pd.to_datetime(curve["date"]))[col]
    m = c.resample("ME").last()
    prev = m.shift(1).fillna(c.iloc[0])
    r = m / prev - 1
    return [dict(year=int(i.year), month=int(i.month), ret=float(v)) for i, v in r.items()]


def monte_carlo(curve: pd.DataFrame, col: str = "options", start: float = 25_000, target: float = 10_000_000,
                days: int = 252, sims: int = 4000, scales=(0.5, 1.0, 2.0, 3.0, 5.0), seed: int = 1) -> dict:
    """Bootstrap the strategy's daily returns. `scale` multiplies position size (risk per trade).

    Answers: with this edge, what is the distribution of 1-year outcomes, and how often does
    the account hit the target or get wiped out at each sizing level.
    """
    eq = curve[col].values.astype(float)
    daily = np.diff(eq) / eq[:-1]
    rng = np.random.default_rng(seed)
    out = []
    for sc in scales:
        draws = rng.choice(daily, size=(sims, days)) * sc
        paths = start * np.cumprod(np.maximum(1 + draws, 0.0), axis=1)
        end = paths[:, -1]
        peak = np.maximum.accumulate(paths, axis=1)
        mdd = (paths / peak - 1).min(axis=1)
        pcts = np.percentile(paths, [5, 25, 50, 75, 95], axis=0)
        step = max(1, days // 60)
        out.append(dict(scale=sc, p_target=float((paths.max(axis=1) >= target).mean()),
                        p_double=float((end >= 2 * start).mean()),
                        p_loss=float((end < start).mean()),
                        p_ruin=float((end < 0.2 * start).mean()),
                        p_dd50=float((mdd <= -0.5).mean()),
                        median_end=float(np.median(end)), p5_end=float(np.percentile(end, 5)),
                        p95_end=float(np.percentile(end, 95)),
                        fan=[dict(day=int(d), p5=float(pcts[0, d]), p25=float(pcts[1, d]), p50=float(pcts[2, d]),
                                  p75=float(pcts[3, d]), p95=float(pcts[4, d])) for d in range(0, days, step)]))
    required = (target / start) ** (1 / days) - 1
    return dict(start=start, target=target, days=days, required_daily=float(required),
                hist_mean_daily=float(daily.mean()), hist_median_daily=float(np.median(daily)),
                scenarios=out)
