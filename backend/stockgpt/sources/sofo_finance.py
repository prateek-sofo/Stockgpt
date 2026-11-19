"""Sofo World -> StockGPT financials, filings and earnings dates (read-only).

Sofo World is the one place that scrapes. This module only reads what its SEC crawlers stored and
reshapes it into the tables the model already uses, so nothing downstream changes:

  observations  field 'metric.<name>'  (FN01, XBRL company facts; published_at = the filing date)
        -> sec_fundamentals.parquet    growth and margin ratios, dated by the day they became public
  observations  field 'filing'         (ID04/FN03: form, filed, accession, items, url)
        -> filings.parquet             every filing in plain words (the Market and stock pages)
        -> sec_earnings.parquet        8-K item 2.02 = an earnings release
  listed (optional table)              ticker, name, exchange, cik, sic
        -> sec_tickers.parquet, listed.parquet

Only rows with license_scope 'public' are read. Works against the database (read-only role) or
against table dumps in a folder or s3:// prefix.

    SOFO_DATABASE_URL=postgresql://sofo_stockgpt@.../sofo_world python -m stockgpt.sources.sofo_finance
    SOFO_NEWS_URI=s3://bucket/stockgpt python -m stockgpt.sources.sofo_finance
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict

import numpy as np
import pandas as pd

from ..config import DATA_DIR
from .filings import label
from .sec import DURATION, _quarterly, company_frame, finish, sic_sector

# Sofo World metric name -> the concept the ratio builder uses
METRICS = {"revenue": "revenue", "gross_profit": "gross_profit", "operating_income": "operating_income",
           "net_income": "net_income", "eps_diluted": "eps", "eps": "eps", "cash_from_operations": "cfo",
           "capex": "capex", "cash": "cash", "long_term_debt": "debt", "shares_outstanding": "shares"}
NEEDED = ["revenue", "gross_profit", "operating_income", "net_income", "eps", "cfo", "capex", "cash", "debt", "shares"]

# the view is created by infra/aws/grants.sql: public rows of the SEC crawlers only
OBS_SQL = "SELECT cik, field, value, published_at, source_id FROM stockgpt_sec_observations WHERE published_at >= %(since)s"


def _obj(v):
    return json.loads(v) if isinstance(v, str) else v


def _clean(obs: pd.DataFrame) -> pd.DataFrame:
    obs = obs.copy()
    if "cik" not in obs and "entity_ref" in obs:
        obs["cik"] = obs["entity_ref"].map(lambda r: (_obj(r) or {}).get("cik"))
    obs["cik"] = pd.to_numeric(obs["cik"].astype(str).str.lstrip("0"), errors="coerce")
    obs = obs.dropna(subset=["cik"])
    obs["cik"] = obs["cik"].astype(int)
    obs["value"] = obs["value"].map(_obj)
    return obs


def financials(obs: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Quarterly ratios per company, each dated by the first filing that reported it."""
    m = obs[obs["field"].str.startswith("metric.")]
    units: dict[int, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    seen = set()
    for cik, field, v, pub in zip(m["cik"], m["field"], m["value"], m["published_at"]):
        concept = METRICS.get(field[7:])
        if concept is None or not isinstance(v, dict) or v.get("value") is None or not v.get("end"):
            continue
        filed = v.get("filed") or (str(pd.Timestamp(pub).date()) if pd.notna(pub) else None)
        if not filed:
            continue                               # without a filing date the value cannot be placed in time
        seen.add(concept)
        u = dict(end=v["end"], val=float(v["value"]), filed=filed, form=v.get("form") or "10-Q")
        if v.get("start"):
            u["start"] = v["start"]
        units[cik][concept].append(u)
    out = []
    for cik, by in units.items():
        series: dict[str, dict[str, dict]] = defaultdict(dict)
        for concept, us in by.items():
            for r in _quarterly(us, concept in DURATION):
                cur = series[concept].get(r["end"])
                if cur is None or r["filed"] < cur["filed"]:
                    series[concept][r["end"]] = r
        frame = company_frame(cik, series)
        if frame is not None:
            out.append(frame)
    coverage = dict(companies=len(out), metrics_present=sorted(seen), metrics_missing=[c for c in NEEDED if c not in seen])
    return finish(out), coverage


def filings(obs: pd.DataFrame) -> pd.DataFrame:
    f = obs[obs["field"] == "filing"]
    rows = []
    for cik, v in zip(f["cik"], f["value"]):
        if not isinstance(v, dict) or not v.get("form") or not v.get("filed"):
            continue
        items = v.get("items") or []
        items = ",".join(items) if isinstance(items, (list, tuple, np.ndarray)) else str(items)
        lab = label(v["form"], items)
        if lab is None:
            continue
        rows.append(dict(date=pd.Timestamp(v["filed"]), cik=cik, form=v["form"], items=items, url=v.get("url") or "",
                         accession=v.get("accession") or "", accepted=v.get("accepted") or v.get("acceptance_datetime"), **lab))
    cols = ["date", "cik", "form", "items", "title", "why", "tone", "importance", "url", "accession", "accepted"]
    return pd.DataFrame(rows, columns=cols).drop_duplicates(["cik", "accession"])


def earnings(fil: pd.DataFrame) -> pd.DataFrame:
    """8-K item 2.02. With the EDGAR acceptance time we know before-open vs after-close; without it
    the release is treated as after the close (first tradable reaction: the next session)."""
    e = fil[fil["form"].str.startswith("8-K") & fil["items"].str.contains("2.02", regex=False)].copy()
    ts = pd.to_datetime(e["accepted"], utc=True, errors="coerce").dt.tz_convert("America/New_York")
    hour = ts.dt.hour + ts.dt.minute / 60
    e["report_date"] = ts.dt.tz_localize(None).dt.normalize().fillna(e["date"])
    e["timing"] = np.where(ts.notna() & (hour < 9.5), "BMO", "AMC")
    e["timing_known"] = ts.notna().values
    return e[["cik", "report_date", "timing", "timing_known"]].drop_duplicates(["cik", "report_date"])


def build(obs: pd.DataFrame, listed: pd.DataFrame | None, min_date: str = "2015-01-01") -> dict:
    obs = _clean(obs)
    if listed is not None and len(listed):
        t = listed.copy()
        t["ticker"] = t["ticker"].astype(str).str.upper()
        t["cik"] = pd.to_numeric(t["cik"], errors="coerce").astype("Int64")
        t = t.dropna(subset=["cik"]).astype({"cik": int})
        for c in ("name", "exchange", "sic", "industry"):
            if c not in t:
                t[c] = ""
        t["sic"] = t["sic"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True)
        t["sector"] = t["sic"].map(sic_sector)
        t = t[["ticker", "name", "exchange", "cik", "sic", "industry", "sector"]]
    else:
        t = pd.read_parquet(DATA_DIR / "sec_tickers.parquet")     # the listing already on disk
    cik2tk = t.drop_duplicates("cik").set_index("cik")["ticker"]
    fund, coverage = financials(obs)
    fil = filings(obs)
    earn = earnings(fil)
    for df in (fund, earn):
        df["ticker"] = df["cik"].map(cik2tk) if len(df) else pd.Series(dtype=str)
    fil = fil.merge(t[["cik", "ticker"]], on="cik")               # every share class of the filer
    keep = pd.Timestamp(min_date)
    if len(fund):
        fund["report_date"] = pd.to_datetime(fund["report_date"])
        fund = fund[fund["report_date"] >= keep].dropna(subset=["ticker"])
    if len(earn) and len(fund):                                   # EPS actual: first quarterly EPS filed on or after the release
        f = fund[["ticker", "report_date", "eps", "eps_growth_yoy"]].rename(columns={"report_date": "filed"})
        earn = pd.merge_asof(earn.dropna(subset=["ticker"]).sort_values("report_date"), f.sort_values("filed"),
                             left_on="report_date", right_on="filed", by="ticker", direction="forward",
                             tolerance=pd.Timedelta(days=45))
        earn = earn.rename(columns={"eps": "eps_actual", "eps_growth_yoy": "eps_surprise_proxy"}).drop(columns="filed")
    coverage.update(filings=len(fil), filings_through=str(fil["date"].max().date()) if len(fil) else None,
                    earnings=len(earn), earnings_timing_known=float(earn["timing_known"].mean()) if len(earn) else None,
                    tickers=int(t["ticker"].nunique()))
    return dict(tickers=t, fundamentals=fund, earnings=earn[earn["report_date"] >= keep] if len(earn) else earn,
                filings=fil[["date", "ticker", "cik", "form", "items", "title", "why", "tone", "importance", "url", "accession"]],
                coverage=coverage)


def write(out: dict, min_companies: int | None = None) -> dict:
    """Write each table only if Sofo World covers enough of the market to replace the direct read;
    otherwise leave the existing table alone and record why. Returns {table: source or reason}."""
    need = min_companies if min_companies is not None else int(os.getenv("STOCKGPT_SOFO_MIN_COMPANIES", "1500"))
    c = out["coverage"]
    sj = DATA_DIR / "sources.json"
    note = json.loads(sj.read_text()) if sj.exists() else {}
    if c["companies"] >= need:
        out["tickers"].to_parquet(DATA_DIR / "sec_tickers.parquet", index=False)
        out["tickers"][["ticker", "name", "cik", "exchange"]].assign(exchange=lambda d: d["exchange"].replace("", "Other")) \
            .to_parquet(DATA_DIR / "listed.parquet", index=False)
        out["fundamentals"].to_parquet(DATA_DIR / "sec_fundamentals.parquet", index=False)
        note["financials"] = "Sofo World"
    else:
        note["financials"] = f"not used: Sofo World has financials for {c['companies']:,} companies, {need:,} needed"
    n_fil = out["filings"]["ticker"].nunique() if len(out["filings"]) else 0
    fresh = len(out["filings"]) and out["filings"]["date"].max() >= pd.Timestamp.now().normalize() - pd.Timedelta(days=5)
    if n_fil >= need and fresh:
        out["filings"].to_parquet(DATA_DIR / "filings.parquet", index=False)
        out["earnings"].to_parquet(DATA_DIR / "sec_earnings.parquet", index=False)
        note["filings"] = note["earnings"] = "Sofo World"
    else:
        note["filings"] = note["earnings"] = (f"not used: Sofo World has filings for {n_fil:,} companies through "
                                              f"{c['filings_through']}, {need:,} companies and the last 5 days needed")
    note["sofo_coverage"] = c
    sj.write_text(json.dumps(note, indent=1))
    return {k: note[k] for k in ("financials", "filings", "earnings")}


if __name__ == "__main__":
    from .sofo_world import _read_any
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2015-01-01")
    ap.add_argument("--dsn", default=os.getenv("SOFO_DATABASE_URL"))
    ap.add_argument("--dump", default=os.getenv("SOFO_NEWS_URI"), help="folder or s3:// prefix with observations/ and listed/")
    a = ap.parse_args()
    if a.dump:
        obs, listed = _read_any(f"{a.dump.rstrip('/')}/observations"), _read_any(f"{a.dump.rstrip('/')}/listed")
    elif a.dsn:
        import psycopg
        with psycopg.connect(a.dsn, options="-c default_transaction_read_only=on") as conn:
            obs = pd.read_sql(OBS_SQL, conn, params=dict(since=a.since))
        listed = None
    else:
        raise SystemExit("set SOFO_NEWS_URI (dump) or SOFO_DATABASE_URL (read-only role)")
    if not len(obs):
        raise SystemExit("Sofo World returned no financial or filing observations")
    out = build(obs, listed if listed is not None and len(listed) else None, a.since)
    used = write(out)
    c = out["coverage"]
    print(f"financials: {len(out['fundamentals']):,} rows, {c['companies']:,} companies; missing metrics: {c['metrics_missing'] or 'none'}")
    print(f"filings: {c['filings']:,} through {c['filings_through']}; earnings releases: {c['earnings']:,} "
          f"(time of day known for {0 if c['earnings_timing_known'] is None else c['earnings_timing_known']:.0%})")
    for k, v in used.items():
        print(f"  {k}: {v}")
