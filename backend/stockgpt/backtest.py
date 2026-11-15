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
            hist = pd.concat(preds).join(panel["z_oc"])
            model.calibrate(hist["mu"].values, hist["z_oc"].values)
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


def build_signals(panel: pd.DataFrame, preds: pd.DataFrame) -> pd.DataFrame:
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


def _select_stock(day: pd.DataFrame, n: int):
    """Stock day trades: rank by expected return in bps after round-trip slippage."""
    cost = 2 * SETTINGS.costs.stock_slippage_bps / 1e4
    er = (2 * day["conf"] - 1) * day["exp_move_pct"] - cost
    c = day[day["liquid"] & (day["conf"] >= SETTINGS.risk.min_edge_prob) & (er > 0)]
    return c.assign(exp_ret=er[c.index]).nlargest(n, "exp_ret")


def _select(day: pd.DataFrame):
    r = SETTINGS.risk
    c = day[day["liquid"] & (day["conf"] >= r.min_edge_prob) & (day["ev_cal"] > r.min_ev)]
    return c.nlargest(r.max_positions, "ev_cal")


def simulate(sig: pd.DataFrame, straddles: bool = True) -> dict:
    """straddles=False when IV is estimated: a straddle's edge is (realised move - implied move), and
    an estimated implied move makes that number an artefact of the estimate, not a measurement."""
    r = SETTINGS.risk
    eq_o = eq_s = eq_e = r.starting_capital
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
        sp = _select_stock(day, r.max_positions)
        if len(sp):
            notional = r.stock_leverage / max(len(sp), r.max_positions)
            pnl_s = float((eq_s * notional * sp["stock_ret"].values).sum())
            for (dt, tk), row in zip(sp.index, sp.itertuples()):
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
        day_ret_o = pnl_o / eq_o
        eq_o += pnl_o
        eq_s += pnl_s
        eq_e += pnl_e
        stress = 0.5 if day_ret_o < -r.daily_loss_stop else 1.0
        curve.append(dict(date=str(date.date()), options=round(eq_o, 2), stock=round(eq_s, 2),
                          straddle=round(eq_e, 2), n=len(picks)))
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
