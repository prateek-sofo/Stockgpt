"""Option pricing and trade construction (vectorised Black-Scholes).

The backtest prices options from the underlying's path and the 30d ATM implied vol. This is an
approximation: real fills depend on the full chain, skew and intraday IV. When you have
historical chains (ORATS / ThetaData / Polygon), replace `price` with chain lookups; the rest of
the pipeline does not change.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import norm

from .config import SETTINGS


def bs_price(S, K, T, sigma, r, is_call):
    S, K, T, sigma = map(np.asarray, (S, K, T, sigma))
    T = np.maximum(T, 1e-6)
    sigma = np.maximum(sigma, 1e-4)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    call = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    put = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    return np.where(is_call, call, put)


def bs_delta(S, K, T, sigma, r, is_call):
    T = np.maximum(T, 1e-6)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    return np.where(is_call, norm.cdf(d1), norm.cdf(d1) - 1)


def strike_grid(S):
    """Listed-strike spacing approximation."""
    S = np.asarray(S, float)
    step = np.select([S < 25, S < 100, S < 250], [0.5, 1.0, 2.5], 5.0)
    return step


def atm_strike(S):
    step = strike_grid(S)
    return np.round(np.asarray(S) / step) * step


def strike_for(S, iv, dte, is_call, moneyness):
    """moneyness = number of weekly std-devs in the money (0 = ATM, 0.8 = ~delta 0.75)."""
    sd = np.asarray(iv) * np.sqrt(dte / 365)
    K = np.where(is_call, S * np.exp(-moneyness * sd), S * np.exp(moneyness * sd))
    step = strike_grid(S)
    return np.round(K / step) * step


# Theta for one regular session, in calendar days. Options desks run a trading-time clock: the
# 6.5h session carries most of a day's variance, so open->close decay ~ one full day.
SESSION_THETA_DAYS = 1.0


def day_trade_return(S0, S1, iv0, iv1, is_call, spread_pct, dte=None, held_days=SESSION_THETA_DAYS,
                     moneyness=0.0, K=None):
    """Return on premium for buying an option at S0 (open) and selling at S1 (close).

    spread_pct is the ATM bid-ask as a fraction of ATM mid. Spreads are roughly constant in
    dollars across nearby strikes, so for in-the-money strikes the % spread is scaled by
    ATM premium / ITM premium (ITM options are cheaper to trade per dollar of exposure).
    held_days: theta charged for the hold (see SESSION_THETA_DAYS).
    """
    c = SETTINGS.costs
    dte = dte or SETTINGS.options.days_to_expiry
    r = SETTINGS.options.risk_free_rate
    if K is None:
        K = strike_for(S0, iv0, dte, is_call, moneyness)
    T0, T1 = dte / 365, (dte - held_days) / 365
    p0 = bs_price(S0, K, T0, iv0, r, is_call)
    p_atm = bs_price(S0, atm_strike(S0), T0, iv0, r, is_call)
    sp = spread_pct * np.clip(p_atm / np.maximum(p0, 1e-6), 0.2, 1.0)
    p1 = bs_price(S1, K, T1, iv1, r, is_call)
    buy = p0 * (1 + sp * c.option_spread_capture)
    sell = p1 * (1 - sp * c.option_spread_capture)
    fees = 2 * c.option_commission_per_contract / 100     # per share-equivalent
    ret = (sell - buy - fees) / np.maximum(buy, 1e-6)
    return ret, K, p0, buy


def straddle_return(S0, S1, iv0, iv1, spread_pct, dte=None, held_days=1.0):
    """Buy ATM call+put at S0 (prior close), sell both at S1 (reaction-day close)."""
    c = SETTINGS.costs
    dte = dte or SETTINGS.options.days_to_expiry
    r = SETTINGS.options.risk_free_rate
    K = atm_strike(S0)
    T0, T1 = dte / 365, (dte - held_days) / 365
    p0 = bs_price(S0, K, T0, iv0, r, True) + bs_price(S0, K, T0, iv0, r, False)
    p1 = bs_price(S1, K, T1, iv1, r, True) + bs_price(S1, K, T1, iv1, r, False)
    buy = p0 * (1 + spread_pct * c.option_spread_capture)
    sell = p1 * (1 - spread_pct * c.option_spread_capture)
    fees = 4 * c.option_commission_per_contract / 100
    return (sell - buy - fees) / np.maximum(buy, 1e-6), K, p0, buy


_GH_X, _GH_W = np.polynomial.hermite_e.hermegauss(9)       # nodes for N(0,1)
_GH_W = _GH_W / _GH_W.sum()
MONEYNESS_CHOICES = (0.0, 0.8)


def expected_day_trade(S0, p_up, exp_move_pct, iv, spread_pct, iv_exit=None,
                       moneyness_choices=MONEYNESS_CHOICES):
    """Model-implied expected return on premium of the directional option trade.

    The open-to-close log return is modelled as N(mu, s) with s set so E|r| equals the
    predicted move and mu set so P(r > 0) equals the calibrated probability. The option P&L
    is integrated over that distribution (Gauss-Hermite), for each candidate strike; the best
    strike per row is chosen.
    Returns dict of arrays: is_call, ev, win (avg return when profitable), loss, conf,
    p_profit, moneyness.
    """
    from scipy.stats import norm as _n
    p_up = np.clip(np.asarray(p_up, float), 0.01, 0.99)
    is_call = p_up >= 0.5
    conf = np.where(is_call, p_up, 1 - p_up)
    s = np.maximum(np.asarray(exp_move_pct, float), 1e-4) / np.sqrt(2 / np.pi)
    mu = _n.ppf(p_up) * s
    best = None
    for m in moneyness_choices:
        iv1 = iv if iv_exit is None else iv_exit
        rets = np.stack([day_trade_return(S0, S0 * np.exp(mu + s * x), iv, iv1, is_call, spread_pct,
                                          moneyness=m)[0] for x in _GH_X])          # (9, n)
        ev = (_GH_W[:, None] * rets).sum(0)
        pos = rets > 0
        p_profit = (_GH_W[:, None] * pos).sum(0)
        win = np.where(p_profit > 0, (_GH_W[:, None] * rets * pos).sum(0) / np.maximum(p_profit, 1e-9), 0)
        loss = np.where(p_profit < 1, (_GH_W[:, None] * rets * ~pos).sum(0) / np.maximum(1 - p_profit, 1e-9), 0)
        cur = dict(ev=ev, win=win, loss=loss, p_profit=p_profit, moneyness=np.full(len(ev), m))
        if best is None:
            best = cur
        else:
            take = ev > best["ev"]
            best = {k: np.where(take, cur[k], best[k]) for k in best}
    best.update(is_call=is_call, conf=conf)
    return best


def kelly_fraction(conf, win, loss):
    """Kelly for a two-outcome bet: f* = p/|loss| - (1-p)/win (fractions of premium)."""
    win = np.maximum(win, 1e-6)
    loss = np.maximum(-loss, 1e-6)
    f = conf / loss - (1 - conf) / win
    return np.clip(f, 0, 1)
