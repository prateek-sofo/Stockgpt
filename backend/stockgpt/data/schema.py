"""Data contract. Drop files matching these schemas into data/raw/ (parquet or csv).

Your scraper only needs to emit these columns. Everything downstream (features, per-ticker
models, backtest, morning scan, UI) works off these tables.

All timestamps are UTC. All dates are exchange (US/Eastern) trading dates.
"""
from __future__ import annotations

TABLES: dict[str, dict] = {
    "tickers": {
        "required": ["ticker", "name", "sector"],
        "optional": ["market_cap", "industry"],
        "doc": "One row per symbol.",
    },
    "prices": {
        "required": ["date", "ticker", "open", "high", "low", "close", "volume"],
        "optional": ["premarket"],
        "doc": "Daily OHLCV, split/dividend adjusted. 'premarket' = last premarket trade "
               "around 09:00 ET (used by the morning scan to see the gap before the open).",
    },
    "news": {
        "required": ["published_at", "ticker", "headline"],
        "optional": ["body", "source", "url"],
        "doc": "One row per (article, ticker). An article mentioning 3 tickers = 3 rows. "
               "published_at must be the real publication time (UTC), not the scrape time, "
               "or the backtest will leak future information.",
    },
    "earnings": {
        "required": ["ticker", "report_date", "timing"],
        "optional": ["eps_actual", "eps_estimate", "revenue_actual", "revenue_estimate"],
        "doc": "Past and scheduled reports. timing in {'BMO','AMC'} (before open / after close). "
               "Future rows have null actuals.",
    },
    "fundamentals": {
        "required": ["ticker", "report_date"],
        "optional": ["revenue_growth_yoy", "gross_margin", "operating_margin", "fcf_margin",
                     "net_debt_to_ebitda", "pe_ratio", "eps_growth_yoy"],
        "doc": "Quarterly snapshot, keyed by the date it became public (report_date).",
    },
    "options": {
        "required": ["date", "ticker", "iv30", "option_volume"],
        "optional": ["iv_weekly", "atm_spread_pct", "open_interest", "iv_rank"],
        "doc": "Daily end-of-day options summary: 30d ATM implied vol (annualised, e.g. 0.42), "
               "iv_weekly = front-week ATM IV (carries the earnings event premium), total "
               "contracts traded, ATM bid-ask spread as fraction of mid. Sources: ORATS, "
               "ThetaData, Polygon, CBOE DataShop.",
    },
}


def validate(name: str, df) -> list[str]:
    """Return a list of problems (empty list = OK)."""
    spec = TABLES[name]
    missing = [c for c in spec["required"] if c not in df.columns]
    return [f"{name}: missing column '{c}'" for c in missing]
