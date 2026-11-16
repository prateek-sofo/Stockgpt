"""SEC EDGAR -> StockGPT data contract (tickers, fundamentals, earnings).

Inputs are SEC's own bulk files (one download each, kinder to EDGAR than ~14k API calls):
  data/raw/sec/company_tickers_exchange.json   https://www.sec.gov/files/company_tickers_exchange.json
  data/raw/sec/companyfacts.zip                https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip
  data/raw/sec/submissions.zip                 https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip

Point-in-time rules:
  * fundamentals are keyed by the date the filing was *filed*, never the period end;
  * earnings dates come from 8-K Item 2.02 (Results of Operations) filings, and the acceptance
    timestamp decides BMO (before 09:30 ET) vs AMC (after; intraday releases are treated as AMC
    so the model never trades a reaction it could not have seen at 09:00).

    python -m stockgpt.sources.sec            # writes tickers / fundamentals / earnings parquet
"""
from __future__ import annotations

import json
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import DATA_DIR

SEC_DIR = DATA_DIR / "sec"
EXCHANGES = {"NYSE", "Nasdaq", "NYSE American", "NYSE Arca", "CBOE", "BATS"}

# concept -> list of us-gaap tags tried in order
CONCEPTS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "equity": ["StockholdersEquity"],
}
DURATION = {"revenue", "gross_profit", "operating_income", "net_income", "eps", "cfo", "capex"}


def load_tickers(path: Path | None = None) -> pd.DataFrame:
    j = json.loads(Path(path or SEC_DIR / "company_tickers_exchange.json").read_text())
    df = pd.DataFrame(j["data"], columns=j["fields"])          # cik, name, ticker, exchange
    df = df[df["exchange"].isin(EXCHANGES)].copy()
    df["ticker"] = df["ticker"].str.upper().str.replace("-", ".", regex=False)
    df["cik"] = df["cik"].astype(int)
    return df.drop_duplicates("ticker")


def _quarterly(units: list[dict], duration: bool) -> list[dict]:
    """Keep one value per (fiscal period end) from 10-Q/10-K, first filed. Durations: 3-month only,
    with Q4 derived as FY minus the three quarters when the 10-K reports only the annual figure."""
    rows = [u for u in units if u.get("form") in ("10-Q", "10-K", "10-Q/A", "10-K/A") and "filed" in u]
    if not duration:
        return [dict(end=u["end"], val=u["val"], filed=u["filed"], form=u["form"]) for u in rows]
    q, fy = [], []
    for u in rows:
        if "start" not in u:
            continue
        days = (pd.Timestamp(u["end"]) - pd.Timestamp(u["start"])).days
        if 80 <= days <= 100:
            q.append(dict(end=u["end"], val=u["val"], filed=u["filed"], form=u["form"]))
        elif 350 <= days <= 380:
            fy.append(dict(start=u["start"], end=u["end"], val=u["val"], filed=u["filed"], form=u["form"]))
    have = {r["end"] for r in q}
    for a in fy:                                              # derive Q4 = FY - (Q1+Q2+Q3)
        if a["end"] in have:
            continue
        inside = [r for r in q if a["start"] < r["end"] < a["end"]]
        ends = {r["end"]: r for r in sorted(inside, key=lambda r: r["filed"])}
        if len(ends) == 3:
            q.append(dict(end=a["end"], val=a["val"] - sum(r["val"] for r in ends.values()),
                          filed=a["filed"], form=a["form"]))
    return q


def fundamentals(ciks: set[int], zip_path: Path | None = None) -> pd.DataFrame:
    """One row per (ticker CIK, filing date) with growth / margin ratios known on that date."""
    out = []
    with zipfile.ZipFile(zip_path or SEC_DIR / "companyfacts.zip") as z:
        for name in z.namelist():
            try:
                cik = int(name.replace("CIK", "").replace(".json", ""))
            except ValueError:
                continue
            if cik not in ciks:
                continue
            facts = json.loads(z.read(name)).get("facts", {})
            gaap = facts.get("us-gaap", {})
            series: dict[str, dict[str, dict]] = defaultdict(dict)
            for concept, tags in CONCEPTS.items():
                for tag in tags:
                    node = gaap.get(tag)
                    if not node:
                        continue
                    units = node["units"].get("USD/shares" if concept == "eps" else "USD")
                    if not units:
                        continue
                    # tags are in priority order; a lower-priority tag only fills periods still empty
                    seen = set(series[concept])
                    for r in _quarterly(units, concept in DURATION):
                        if r["end"] in seen:
                            continue
                        cur = series[concept].get(r["end"])
                        if cur is None or r["filed"] < cur["filed"]:     # first-reported value
                            series[concept][r["end"]] = r
            # shares outstanding (cover page, dei) -> market cap at any later date
            dei = facts.get("dei", {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
            for r in _quarterly(dei, duration=False):
                cur = series["shares"].get(r["end"])
                if cur is None or r["filed"] < cur["filed"]:
                    series["shares"][r["end"]] = r
            if "revenue" not in series:
                continue
            df = pd.DataFrame({c: {e: r["val"] for e, r in s.items()} for c, s in series.items()})
            filed = pd.DataFrame({c: {e: r["filed"] for e, r in s.items()} for c, s in series.items()})
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            filed = filed.apply(pd.to_datetime)
            df["filed"] = filed.reindex(df.index.strftime("%Y-%m-%d")).max(axis=1).values
            # YoY on the quarter four periods back (matched by period end within ~1 year)
            # same quarter one year earlier, matched by period end (+/- 20 days), not by position
            prev = df.reindex(df.index - pd.Timedelta(days=364), method="nearest",
                              tolerance=pd.Timedelta(days=20))
            prev.index = df.index
            ttm_rev = df["revenue"].rolling(4).sum()
            out.append(pd.DataFrame({
                "cik": cik, "period_end": df.index, "report_date": df["filed"].values,
                "revenue": df["revenue"].values,
                "revenue_growth_yoy": (df["revenue"] / prev["revenue"] - 1).values,
                "gross_margin": (df.get("gross_profit") / df["revenue"]).values if "gross_profit" in df else np.nan,
                "operating_margin": (df.get("operating_income") / df["revenue"]).values if "operating_income" in df else np.nan,
                "net_margin": (df.get("net_income") / df["revenue"]).values if "net_income" in df else np.nan,
                "fcf_margin": ((df.get("cfo") - df.get("capex").fillna(0)) / df["revenue"]).values
                if "cfo" in df and "capex" in df else np.nan,
                "eps": df.get("eps", pd.Series(np.nan, index=df.index)).values,
                "eps_growth_yoy": ((df["eps"] - prev["eps"]) / prev["eps"].abs().replace(0, np.nan)).values
                if "eps" in df else np.nan,
                "net_debt_to_revenue": ((df.get("debt", 0) - df.get("cash", 0)) / ttm_rev).values
                if "debt" in df or "cash" in df else np.nan,
                "shares": df.get("shares", pd.Series(np.nan, index=df.index)).values,
            }))
    if not out:
        return pd.DataFrame()
    f = pd.concat(out, ignore_index=True)
    num = f.select_dtypes("number").columns.drop(["cik"])
    f[num] = f[num].replace([np.inf, -np.inf], np.nan)
    for c in ("revenue_growth_yoy", "eps_growth_yoy"):
        f[c] = f[c].clip(-5, 5)
    for c in ("gross_margin", "operating_margin", "net_margin", "fcf_margin"):
        f[c] = f[c].clip(-3, 1)
    return f.dropna(subset=["report_date"])


def earnings_dates(ciks: set[int], zip_path: Path | None = None) -> pd.DataFrame:
    """8-K Item 2.02 filings -> (cik, report_date, timing) using the EDGAR acceptance time."""
    rows, sics = [], {}
    with zipfile.ZipFile(zip_path or SEC_DIR / "submissions.zip") as z:
        for name in z.namelist():
            if not name.startswith("CIK") or "-submissions-" in name:
                continue
            try:
                cik = int(name[3:13])
            except ValueError:
                continue
            if cik not in ciks:
                continue
            sub = json.loads(z.read(name))
            sics[cik] = (sub.get("sic") or "", sub.get("sicDescription") or "")
            rec = sub.get("filings", {}).get("recent", {})
            forms, items, acc = rec.get("form", []), rec.get("items", []), rec.get("acceptanceDateTime", [])
            for f, it, a in zip(forms, items, acc):
                if f in ("8-K", "8-K/A") and "2.02" in (it or ""):
                    rows.append((cik, a))
    earnings_dates.sic = sics                     # stash for build()
    df = pd.DataFrame(rows, columns=["cik", "accepted"])
    if df.empty:
        return pd.DataFrame(columns=["cik", "report_date", "timing"])
    ts = pd.to_datetime(df["accepted"], utc=True).dt.tz_convert("America/New_York")
    df["report_date"] = ts.dt.tz_localize(None).dt.normalize()
    hour = ts.dt.hour + ts.dt.minute / 60
    df["timing"] = np.where(hour < 9.5, "BMO", "AMC")
    df["accepted_et"] = ts.dt.strftime("%Y-%m-%d %H:%M")
    return df.drop_duplicates(["cik", "report_date"]).drop(columns="accepted")


def sic_sector(sic: str) -> str:
    """SEC SIC code -> GICS-like sector bucket."""
    try:
        c = int(sic)
    except (TypeError, ValueError):
        return "Other"
    ranges = [
        ((1311, 1389), "Energy"), ((2911, 2999), "Energy"), ((4610, 4619), "Energy"), ((4920, 4925), "Energy"),
        ((2830, 2836), "Healthcare"), ((3841, 3851), "Healthcare"), ((8000, 8099), "Healthcare"), ((5122, 5122), "Healthcare"),
        ((3570, 3579), "Technology"), ((3600, 3699), "Technology"), ((7370, 7379), "Technology"), ((3810, 3829), "Technology"),
        ((4800, 4899), "Communication"), ((2700, 2799), "Communication"), ((7810, 7819), "Communication"),
        ((6000, 6499), "Financials"), ((6700, 6799), "Financials"),
        ((6500, 6599), "Real Estate"), ((6798, 6798), "Real Estate"),
        ((4900, 4919), "Utilities"), ((4926, 4991), "Utilities"),
        ((1000, 1499), "Materials"), ((2600, 2699), "Materials"), ((2800, 2829), "Materials"), ((2840, 2899), "Materials"),
        ((3300, 3399), "Materials"),
        ((2000, 2199), "Consumer Staples"), ((5400, 5499), "Consumer Staples"), ((5140, 5149), "Consumer Staples"),
        ((5000, 5999), "Consumer Disc."), ((7000, 7099), "Consumer Disc."), ((5800, 5899), "Consumer Disc."),
        ((2300, 2399), "Consumer Disc."), ((3711, 3716), "Consumer Disc."), ((7900, 7999), "Consumer Disc."),
        ((1500, 1799), "Industrials"), ((3400, 3569), "Industrials"), ((3580, 3599), "Industrials"),
        ((3700, 3799), "Industrials"), ((4000, 4799), "Industrials"), ((7300, 7369), "Industrials"),
        ((8700, 8799), "Industrials"),
    ]
    if c == 6798:
        return "Real Estate"
    for (lo, hi), name in ranges:
        if lo <= c <= hi:
            return name
    return "Other"


def build(min_date: str = "2015-01-01") -> dict[str, pd.DataFrame]:
    t = load_tickers()
    ciks = set(t["cik"])
    fund = fundamentals(ciks)
    fund["report_date"] = pd.to_datetime(fund["report_date"], errors="coerce")
    fund = fund.dropna(subset=["report_date"])
    earn = earnings_dates(ciks)
    cik2tk = t.drop_duplicates("cik").set_index("cik")["ticker"]   # primary share class per CIK
    fund["ticker"] = fund["cik"].map(cik2tk)
    earn["ticker"] = earn["cik"].map(cik2tk)
    # attach EPS actuals to earnings rows: first 10-Q/10-K EPS filed on/after the 8-K date
    e = earn.sort_values("report_date")
    f = fund[["ticker", "report_date", "eps", "eps_growth_yoy"]].rename(columns={"report_date": "filed"})
    e = pd.merge_asof(e.sort_values("report_date"), f.sort_values("filed").dropna(subset=["ticker"]),
                      left_on="report_date", right_on="filed", by="ticker", direction="forward",
                      tolerance=pd.Timedelta(days=45))
    e = e.rename(columns={"eps": "eps_actual", "eps_growth_yoy": "eps_surprise_proxy"}).drop(columns="filed")
    keep = pd.Timestamp(min_date)
    sic = getattr(earnings_dates, "sic", {})
    t["sic"] = t["cik"].map(lambda c: sic.get(c, ("", ""))[0])
    t["industry"] = t["cik"].map(lambda c: sic.get(c, ("", ""))[1])
    t["sector"] = t["sic"].map(sic_sector)
    tickers = t[["ticker", "name", "exchange", "cik", "sic", "industry", "sector"]]
    return dict(tickers=tickers,
                fundamentals=fund[fund["report_date"] >= keep].dropna(subset=["ticker"]),
                earnings=e[e["report_date"] >= keep].dropna(subset=["ticker"]))


if __name__ == "__main__":
    out = build()
    for k, v in out.items():
        v.to_parquet(DATA_DIR / f"sec_{k}.parquet", index=False)
        print(k, v.shape)
