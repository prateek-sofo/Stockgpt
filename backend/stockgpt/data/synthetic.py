"""Synthetic market generator for demo and testing.

Produces every table in the data contract (tickers, prices, news, earnings, fundamentals,
options) with *hidden per-ticker behaviour* so the per-ticker models have something real to
learn: some names under-react to news (drift after the open), some fade their gaps, some move
hard on earnings, some carry rich implied vol.

Everything is fictional. Replace with your scraped data via data/raw/*.parquet.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SECTORS = ["Technology", "Healthcare", "Financials", "Consumer Disc.", "Industrials",
           "Energy", "Communication", "Consumer Staples", "Materials", "Utilities", "Real Estate"]

_PFX = ["Arc", "Nova", "Helix", "Vertex", "Quant", "Lumen", "Strata", "Orbit", "Cobalt", "Zephyr",
        "Apex", "Ion", "Delta", "Pioneer", "Summit", "Atlas", "Crest", "Nimbus", "Polar", "Sol",
        "Terra", "Vanta", "Kinet", "Echo", "Prism", "Halo", "Bolt", "Cedar", "Ember", "Flux"]
_SFX = {"Technology": ["Systems", "Semiconductor", "Software", "Cloud", "AI"],
        "Healthcare": ["Therapeutics", "Bio", "Medical", "Genomics", "Health"],
        "Financials": ["Capital", "Bancorp", "Financial", "Payments", "Insurance"],
        "Consumer Disc.": ["Retail", "Motors", "Brands", "Apparel", "Travel"],
        "Industrials": ["Aerospace", "Industries", "Logistics", "Machinery", "Rail"],
        "Energy": ["Energy", "Petroleum", "Resources", "Solar", "Midstream"],
        "Communication": ["Media", "Networks", "Telecom", "Interactive", "Studios"],
        "Consumer Staples": ["Foods", "Beverage", "Household", "Grocers", "Farms"],
        "Materials": ["Materials", "Chemicals", "Mining", "Steel", "Packaging"],
        "Utilities": ["Power", "Utilities", "Water", "Grid", "Electric"],
        "Real Estate": ["Realty", "Properties", "REIT", "Land", "Towers"]}

BANKS = ["Goldline", "Morgan Hale", "Barclay Stone", "Piper Ridge", "Evercrest", "Northbridge"]

HEADLINES = {
    "strong_pos": [
        "{co} raises full-year guidance well above consensus",
        "{co} surges after landing multi-billion dollar contract",
        "{co} upgraded to Buy at {bank}, price target raised sharply",
        "{co} announces $2B share buyback and dividend increase",
        "FDA approves {co} lead drug ahead of schedule",
        "{co} record quarterly revenue beats expectations",
        "{co} to be acquired at a 35% premium",
    ],
    "pos": [
        "{co} expands partnership with major cloud provider",
        "{co} shares gain as analysts see strong demand",
        "{co} launches new product line, early orders robust",
        "{co} reports solid growth in subscriber base",
        "{bank} reiterates Outperform on {co}, sees upside",
        "{co} wins approval to expand into new markets",
    ],
    "neutral": [
        "{co} to present at upcoming industry conference",
        "{co} announces date of quarterly earnings call",
        "{co} files annual proxy statement",
        "{co} appoints new board member",
        "{co} schedules investor day for next quarter",
        "What to watch for {co} this week",
    ],
    "neg": [
        "{co} shares slip as competition intensifies",
        "{co} faces supply chain delays, margins under pressure",
        "{bank} trims price target on {co} citing slowing growth",
        "{co} CFO to step down, search for successor underway",
        "{co} sees weaker demand in key region",
        "{co} delays product launch to next year",
    ],
    "strong_neg": [
        "{co} cuts full-year guidance, shares plunge",
        "{co} downgraded to Sell at {bank} on deteriorating fundamentals",
        "SEC opens investigation into {co} accounting practices",
        "{co} recalls flagship product over safety concerns",
        "{co} misses revenue estimates, warns of losses",
        "FDA rejects {co} drug application",
        "{co} hit with major lawsuit, faces billions in damages",
    ],
    "earn_beat": [
        "{co} beats on earnings and revenue, raises outlook",
        "{co} Q{q} EPS tops estimates as margins expand",
        "{co} posts strong quarter, beats Street expectations",
    ],
    "earn_inline": [
        "{co} Q{q} results in line with estimates",
        "{co} reports quarterly results, maintains guidance",
    ],
    "earn_miss": [
        "{co} misses earnings estimates, lowers guidance",
        "{co} Q{q} revenue falls short, shares drop after hours",
        "{co} disappoints with weak quarter and soft outlook",
    ],
}


def _bucket(s: np.ndarray) -> np.ndarray:
    return np.select([s > 0.6, s > 0.2, s > -0.2, s > -0.6],
                     ["strong_pos", "pos", "neutral", "neg"], "strong_neg")


def generate(n_tickers: int = 300, start: str = "2023-11-01", end: str = "2026-11-13",
             seed: int = 7) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, end)
    T, N = len(dates), n_tickers

    # ---------------- ticker universe and hidden traits ----------------
    syms, names, sectors = set(), [], []
    letters = np.array(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    while len(syms) < N:
        syms.add("".join(rng.choice(letters, rng.integers(3, 5))))
    syms = sorted(syms)
    for _ in range(N):
        sec = SECTORS[rng.integers(len(SECTORS))]
        sectors.append(sec)
        names.append(f"{rng.choice(_PFX)}{rng.choice(['', 'a', 'on', 'ix', 'era'])} "
                     f"{rng.choice(_SFX[sec])}")
    sector_idx = np.array([SECTORS.index(s) for s in sectors])

    ann_vol = np.clip(rng.lognormal(np.log(0.38), 0.35, N), 0.15, 1.2)
    sig = ann_vol / np.sqrt(252)
    beta = np.clip(rng.normal(1.0, 0.3, N), 0.3, 2.0)
    mcap = rng.lognormal(np.log(15e9), 1.3, N)
    price0 = np.exp(rng.uniform(np.log(8), np.log(600), N))
    adv_dollar = np.clip(mcap * rng.uniform(0.004, 0.012, N), 5e6, 1e10)

    news_sens = rng.lognormal(0.0, 0.45, N)                 # reaction intensity
    drift_frac = rng.beta(2.0, 4.5, N)                       # under-reaction after the open
    gap_rev = rng.normal(-0.08, 0.12, N)                     # intraday fade/extension of gap noise
    earn_mult = rng.uniform(2.0, 6.5, N)                     # earnings move in daily sigmas
    iv_prem = rng.uniform(0.95, 1.25, N)                     # IV / realised
    quality = rng.normal(0.0, 1.0, N)                        # long-term fundamental trait
    news_rate = np.clip(0.25 + 0.5 * np.log10(mcap / 1e9), 0.1, 2.5)

    # ---------------- market + sector factors with vol regimes ----------------
    mkt_vol = np.empty(T)
    v = 0.010
    for t in range(T):
        v = np.sqrt(0.000002 + 0.94 * v ** 2 + 0.05 * (rng.normal() * v) ** 2)
        mkt_vol[t] = np.clip(v, 0.006, 0.03)
    mkt_gap = rng.normal(0.0002, 1, T) * mkt_vol * 0.45
    mkt_oc = rng.normal(0.0002, 1, T) * mkt_vol * 0.85
    sec_oc = rng.normal(0, 0.006, (T, len(SECTORS)))

    # ---------------- earnings schedule ----------------
    earn_flag_bmo = np.zeros((T, N), bool)    # report before open on day t
    earn_flag_amc = np.zeros((T, N), bool)    # report after close on day t (reaction t+1)
    earn_rows = []
    for i in range(N):
        first = rng.integers(5, 60)
        timing = "BMO" if rng.random() < 0.5 else "AMC"
        for t in range(first, T, 63):
            t2 = min(T - 1, t + rng.integers(-3, 4))
            (earn_flag_bmo if timing == "BMO" else earn_flag_amc)[t2, i] = True
            earn_rows.append((syms[i], dates[t2], timing))
    # Reaction day for AMC is the next session
    earn_react = earn_flag_bmo.copy()
    earn_react[1:] |= earn_flag_amc[:-1]
    surprise = np.where(earn_react, rng.standard_t(4, (T, N)) * 0.7 + 0.15 * quality, 0.0)

    # ---------------- overnight news information ----------------
    n_overnight = rng.poisson(news_rate * 0.6, (T, N))
    n_intraday = rng.poisson(news_rate * 0.4, (T, N))
    # Latent sentiment per news day; heavy tails = occasional big catalysts
    news_info = np.zeros((T, N))
    has_on = n_overnight > 0
    news_info[has_on] = np.clip(rng.standard_t(3, has_on.sum()) * 0.35, -1, 1)
    intraday_info = np.where(n_intraday > 0, np.clip(rng.standard_t(3, (T, N)) * 0.35, -1, 1), 0)

    # Total information known before the open (scaled into return units)
    pre_open_info = news_info + 0.9 * np.tanh(surprise)

    # ---------------- returns ----------------
    k = 0.45
    idio_gap = rng.normal(0, 1, (T, N)) * sig * 0.35
    gap = (beta * mkt_gap[:, None] + news_sens * k * pre_open_info * sig
           + earn_mult * sig * 0.85 * np.tanh(surprise) * 0.0 + idio_gap)
    gap += np.where(earn_react, earn_mult * sig * surprise * 0.8, 0.0)
    # News days are more volatile intraday; size scales with the (observable) opening gap
    vol_mult = np.sqrt(1 + 0.15 * np.minimum((gap / sig) ** 2, 16))
    oc = (beta * mkt_oc[:, None] + sec_oc[:, sector_idx]
          + news_sens * k * drift_frac * pre_open_info * sig            # under-reaction drift
          + np.where(earn_react, earn_mult * sig * surprise * 0.8 * drift_frac * 0.12, 0.0)
          + gap_rev * idio_gap / 0.35 * 0.35                              # gap fade
          + news_sens * 0.6 * intraday_info * sig                         # unknowable at open
          + rng.normal(0, 1, (T, N)) * sig * 0.78 * vol_mult
          + quality * 0.00015)                                            # slow fundamental drift
    gap = np.clip(gap, -0.45, 0.6)
    oc = np.clip(oc, -0.4, 0.5)

    close = np.empty((T, N))
    open_ = np.empty((T, N))
    prev = price0.copy()
    for t in range(T):
        open_[t] = prev * np.exp(gap[t])
        close[t] = open_[t] * np.exp(oc[t])
        prev = close[t]
    rng_hl = np.abs(rng.normal(0, 1, (T, N))) * sig * 0.6
    high = np.maximum(open_, close) * np.exp(rng_hl)
    low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, 1, (T, N))) * sig * 0.6)
    premarket = np.vstack([price0, close[:-1]]) * np.exp(gap * 0.85 + rng.normal(0, 1, (T, N)) * sig * 0.12)

    activity = 1 + 1.8 * np.abs(pre_open_info) + 2.5 * earn_react
    shares_adv = adv_dollar / price0
    volume = (shares_adv * activity * np.exp(rng.normal(0, 0.3, (T, N)))
              * (price0 / np.maximum(close, 0.5)) ** 0.5).astype(np.int64)

    # ---------------- options summary ----------------
    rv = pd.DataFrame(np.log(close / np.vstack([price0, close[:-1]]))).ewm(span=20).std().values * np.sqrt(252)
    rv = np.where(np.isnan(rv), ann_vol[None, :], rv)
    days_to_earn = np.full((T, N), 999)
    tt = np.arange(T)
    for i in range(N):
        idx = np.where(earn_react[:, i])[0]
        if len(idx):
            pos = np.searchsorted(idx, tt)
            ok = pos < len(idx)
            days_to_earn[ok, i] = idx[pos[ok]] - tt[ok]
    # Event variance priced into options. u < 1: market under-prices this name's earnings moves.
    ev_std = 0.8 * earn_mult * sig
    u = rng.uniform(0.85, 1.3, N)
    base = rv * iv_prem
    in_w = (days_to_earn >= 1) & (days_to_earn <= 5)
    in_30 = (days_to_earn >= 1) & (days_to_earn <= 21)
    noise = np.exp(rng.normal(0, 0.04, (T, N)))
    iv_weekly = np.clip(np.sqrt(base ** 2 + in_w * (u * ev_std) ** 2 * 365 / 7) * noise, 0.08, 4.0)
    iv30 = np.clip(np.sqrt(base ** 2 + in_30 * (u * ev_std) ** 2 * 365 / 30) * noise, 0.08, 2.5)
    opt_vol = (adv_dollar / 1e9 * 60_000 * (ann_vol / 0.4) * activity
               * np.exp(rng.normal(0, 0.35, (T, N)))).astype(np.int64)
    atm_spread = np.clip(0.012 + 0.9 / np.sqrt(np.maximum(opt_vol, 50)), 0.012, 0.4)

    # ---------------- assemble tables ----------------
    idx = pd.MultiIndex.from_product([dates, syms], names=["date", "ticker"])
    flat = lambda a: a.reshape(-1)
    prices = pd.DataFrame({"open": flat(open_), "high": flat(high), "low": flat(low),
                           "close": flat(close), "volume": flat(volume),
                           "premarket": flat(premarket)}, index=idx).reset_index()
    options = pd.DataFrame({"iv30": flat(iv30), "iv_weekly": flat(iv_weekly),
                            "option_volume": flat(opt_vol),
                            "atm_spread_pct": flat(atm_spread)}, index=idx).reset_index()

    tickers = pd.DataFrame({"ticker": syms, "name": names, "sector": sectors,
                            "market_cap": mcap.round(-6)})

    earnings = pd.DataFrame(earn_rows, columns=["ticker", "report_date", "timing"])
    # actuals vs estimates derived from the surprise on the reaction day
    sym_i = {s: i for i, s in enumerate(syms)}
    eps_est = rng.uniform(0.2, 3.0, N)
    ea, ee = [], []
    for r in earnings.itertuples():
        i = sym_i[r.ticker]
        t = dates.get_loc(r.report_date) + (1 if r.timing == "AMC" else 0)
        z = surprise[min(t, T - 1), i] if t < T else np.nan
        ee.append(round(eps_est[i], 2))
        ea.append(round(eps_est[i] * (1 + 0.06 * z), 2) if t < T else np.nan)
    earnings["eps_estimate"], earnings["eps_actual"] = ee, ea
    # Add the next scheduled (future) report so the scanner can see upcoming catalysts
    fut = []
    for s in syms:
        last = earnings.loc[earnings.ticker == s, "report_date"].max()
        nxt = last + pd.tseries.offsets.BDay(63)
        fut.append((s, nxt, earnings.loc[earnings.ticker == s, "timing"].iloc[0],
                    round(eps_est[sym_i[s]], 2), np.nan))
    earnings = pd.concat([earnings, pd.DataFrame(fut, columns=earnings.columns)], ignore_index=True)

    fund = []
    for r in earnings.dropna(subset=["eps_actual"]).itertuples():
        i = sym_i[r.ticker]
        fund.append(dict(ticker=r.ticker, report_date=r.report_date,
                         revenue_growth_yoy=0.08 + 0.07 * quality[i] + rng.normal(0, 0.04),
                         gross_margin=np.clip(0.45 + 0.08 * quality[i] + rng.normal(0, 0.03), 0.05, 0.9),
                         operating_margin=np.clip(0.15 + 0.06 * quality[i] + rng.normal(0, 0.03), -0.4, 0.6),
                         fcf_margin=0.10 + 0.05 * quality[i] + rng.normal(0, 0.03),
                         net_debt_to_ebitda=np.clip(1.5 - 0.6 * quality[i] + rng.normal(0, 0.4), -2, 8),
                         eps_growth_yoy=0.10 + 0.12 * quality[i] + rng.normal(0, 0.08),
                         pe_ratio=np.clip(22 + 6 * quality[i] + rng.normal(0, 5), 4, 120)))
    fundamentals = pd.DataFrame(fund)

    # ---------------- news text ----------------
    et = "America/New_York"
    rows = []
    for (kind, counts, info) in (("overnight", n_overnight, news_info),
                                 ("intraday", n_intraday, intraday_info)):
        ts, ns = np.nonzero(counts)
        for t, i in zip(ts, ns):
            for j in range(min(counts[t, i], 4)):
                s = np.clip(info[t, i] + rng.normal(0, 0.15), -1, 1) if j == 0 else rng.normal(0, 0.25)
                b = _bucket(np.array([s]))[0]
                tmpl = HEADLINES[b][rng.integers(len(HEADLINES[b]))]
                if kind == "overnight":
                    # between 16:30 prev session and 08:45 today
                    base = dates[t - 1] if t > 0 else dates[t] - pd.Timedelta(days=1)
                    hrs = rng.uniform(16.5, 24 + 8.75)
                    if dates[t].weekday() == 0 and t > 0:
                        hrs = rng.uniform(16.5, 24 * 3 + 8.75)
                    when = base + pd.Timedelta(hours=float(hrs))
                else:
                    when = dates[t] + pd.Timedelta(hours=float(rng.uniform(9.75, 15.75)))
                rows.append((when, syms[i], tmpl.format(co=names[i], bank=rng.choice(BANKS), q=1)))
    # earnings headlines at report time
    for t, i in zip(*np.nonzero(earn_react)):
        z = surprise[t, i]
        b = "earn_beat" if z > 0.4 else ("earn_miss" if z < -0.4 else "earn_inline")
        q = (dates[t].quarter % 4) + 1
        when = (dates[t] + pd.Timedelta(hours=7.0)) if earn_flag_bmo[t, i] else \
               (dates[t - 1] + pd.Timedelta(hours=16.1))
        tmpl = HEADLINES[b][rng.integers(len(HEADLINES[b]))]
        rows.append((when, syms[i], tmpl.format(co=names[i], q=q)))
    news = pd.DataFrame(rows, columns=["published_at", "ticker", "headline"])
    news["published_at"] = news["published_at"].dt.tz_localize(et, ambiguous="NaT", nonexistent="shift_forward").dt.tz_convert("UTC")
    news = news.dropna(subset=["published_at"])
    news["source"] = rng.choice(["Newswire", "MarketDesk", "StreetWire", "Reuters-like", "PR"], len(news))
    news = news.sort_values("published_at").reset_index(drop=True)

    return dict(tickers=tickers, prices=prices, news=news, earnings=earnings,
                fundamentals=fundamentals, options=options)
