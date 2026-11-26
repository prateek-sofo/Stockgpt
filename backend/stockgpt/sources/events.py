"""Timestamped company events from SEC filings: the one news history we hold with the exact minute
each item became public, for every listed company, back to 2015.

An 8-K is how a company tells the market something happened: results (item 2.02), a deal (1.01,
2.01), a guidance or press release (7.01, 8.01), an executive change (5.02), an impairment (2.06),
a restatement (4.02). EDGAR stamps each with its acceptance time to the second. This module reads
the bulk submissions file and writes one row per filing:

    data/raw/events.parquet   ticker, cik, accepted (UTC), form, items, session (pre|open|post), date

`date` is the first trading session that can react: the same day for a filing accepted before the
close, the next session for one accepted after it.

    python -m stockgpt.sources.events
"""
from __future__ import annotations

import json
import zipfile

import numpy as np
import pandas as pd

from ..config import DATA_DIR
from ..sessions import SESSION

OUT = DATA_DIR / "events.parquet"
FORMS = ("8-K", "8-K/A", "6-K", "10-Q", "10-K", "SC 13D", "SCHEDULE 13D", "SC TO-T", "424B5", "NT 10-Q", "NT 10-K")
ET = "America/New_York"


def build(min_date: str = "2015-01-01") -> pd.DataFrame:
    t = pd.read_parquet(DATA_DIR / "sec_tickers.parquet")[["ticker", "cik"]].drop_duplicates("cik")
    want = dict(zip(t["cik"].astype(int), t["ticker"]))
    rows = []
    with zipfile.ZipFile(DATA_DIR / "sec" / "submissions.zip") as z:
        for name in z.namelist():
            if not name.startswith("CIK"):
                continue
            try:
                cik = int(name[3:13])
            except ValueError:
                continue
            if cik not in want:
                continue
            sub = json.loads(z.read(name))
            rec = sub.get("filings", {}).get("recent", sub)        # older pages are the bare table
            forms, acc = rec.get("form", []), rec.get("acceptanceDateTime", [])
            items, nums = rec.get("items", [""] * len(forms)), rec.get("accessionNumber", [""] * len(forms))
            for f, a, it, n in zip(forms, acc, items, nums):
                if f in FORMS and a and a[:10] >= min_date:
                    rows.append((cik, a, f, it or "", n))
    e = pd.DataFrame(rows, columns=["cik", "accepted", "form", "items", "accession"]).drop_duplicates("accession")
    e["ticker"] = e["cik"].map(want)
    e["accepted"] = pd.to_datetime(e["accepted"], utc=True)
    local = e["accepted"].dt.tz_convert(ET)
    mins = local.dt.hour * 60 + local.dt.minute
    day = local.dt.tz_localize(None).dt.normalize()
    is_session = day.isin(pd.date_range(day.min(), day.max(), freq=SESSION))
    e["session"] = np.where(~is_session, "post", np.where(mins < 9 * 60 + 30, "pre", np.where(mins < 16 * 60, "open", "post")))
    # first session that can react
    nxt = (day + SESSION).dt.normalize()
    e["date"] = np.where(e["session"] == "post", nxt, day)
    e["date"] = pd.to_datetime(e["date"])
    e = e.sort_values("accepted").reset_index(drop=True)
    e.to_parquet(OUT, index=False)
    return e


if __name__ == "__main__":
    e = build()
    k = e[e["form"].str.startswith("8-K")]
    print(f"events: {len(e):,} filings, {e['ticker'].nunique():,} companies, {e['accepted'].min():%Y-%m-%d} -> {e['accepted'].max():%Y-%m-%d}")
    print(f"8-K: {len(k):,}; accepted before the open {np.mean(k['session'] == 'pre'):.0%}, during trading {np.mean(k['session'] == 'open'):.0%}, after the close {np.mean(k['session'] == 'post'):.0%}")
    print(k["items"].str.split(",").explode().value_counts().head(12).to_dict())
