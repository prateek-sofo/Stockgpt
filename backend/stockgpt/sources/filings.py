"""Every SEC filing by every listed company, every day, in plain words.

EDGAR publishes one index per business day listing every filing. This module reads those indexes,
keeps filings by companies with a listed ticker, looks up what each 8-K is about (its item codes,
from the company's EDGAR submissions feed) and labels each filing: what it is, why a shareholder
might care, and whether it usually reads as good news, bad news or something to watch.

    python -m stockgpt.sources.filings --days 45     # data/raw/filings.parquet (appends; reruns are cheap)

SEC fair access: declared User-Agent (SEC_CONTACT), well under 10 requests a second.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import httpx
import pandas as pd

from ..config import DATA_DIR

OUT = DATA_DIR / "filings.parquet"
CACHE = DATA_DIR / "sec" / "daily"
UA = {"User-Agent": f"StockGPT research {os.environ.get('SEC_CONTACT', 'data@sofo.ai')}"}
LINE = re.compile(r"^(.+?)\s{2,}(.+?)\s{2,}(\d+)\s+(\d{8})\s+(edgar/\S+)\s*$")

# 8-K item -> (plain title, why it matters, tone, importance 1-3)
ITEMS = {
    "1.01": ("Signed a major agreement", "A new contract, partnership, loan or deal big enough that it had to be disclosed.", "watch", 2),
    "1.02": ("Ended a major agreement", "An important contract or deal was terminated.", "watch", 2),
    "1.03": ("Filed for bankruptcy", "The company entered bankruptcy or receivership. Shareholders are usually last in line.", "bad", 3),
    "1.05": ("Reported a cybersecurity incident", "A security breach serious enough to disclose.", "bad", 3),
    "2.01": ("Completed buying or selling a business", "An acquisition or a sale of assets has closed.", "watch", 2),
    "2.02": ("Reported financial results", "Earnings are out. This is the most common reason a stock makes a big move.", "watch", 3),
    "2.03": ("Took on new debt", "A new loan, bond or other obligation.", "watch", 2),
    "2.04": ("A debt came due early", "An event let lenders demand faster repayment. Often a sign of financial stress.", "bad", 3),
    "2.05": ("Announced a restructuring", "Layoffs, plant closures or exit costs.", "watch", 3),
    "2.06": ("Wrote down the value of assets", "The company says something it owns is worth materially less than before.", "bad", 3),
    "3.01": ("Got a stock exchange listing warning", "The company fails a listing rule or is moving exchanges. Can lead to delisting.", "bad", 3),
    "3.02": ("Sold new shares privately", "New shares were issued outside the public market. Existing holders own a smaller slice.", "watch", 2),
    "3.03": ("Changed shareholder rights", "The rights attached to the shares were modified.", "watch", 2),
    "4.01": ("Changed its auditor", "A new accounting firm. Worth a look if the old one resigned.", "watch", 2),
    "4.02": ("Said past financial statements cannot be relied on", "Earlier numbers were wrong and will be restated.", "bad", 3),
    "5.01": ("Change in who controls the company", "Control of the company has changed hands.", "watch", 3),
    "5.02": ("Executive or board change", "A senior leader or director left, joined or had pay changed.", "watch", 2),
    "5.03": ("Changed its bylaws", "Governance rules or the fiscal year were amended.", "info", 1),
    "5.07": ("Shareholder vote results", "Results of a shareholder meeting.", "info", 1),
    "5.08": ("Set a shareholder meeting for director nominations", "Dates for nominating directors.", "info", 1),
    "7.01": ("Shared an update with investors", "A presentation, press release or guidance given to all investors at once.", "info", 2),
    "8.01": ("Announced other news", "News the company chose to disclose: often a press release, a dividend, a buyback or a legal update.", "info", 2),
}
# form prefix -> (title, why, tone, importance); first match wins
FORMS = [
    ("10-K", "Annual report", "The full-year audited report: results, risks and outlook.", "info", 3),
    ("10-Q", "Quarterly report", "The detailed quarterly financial statements.", "info", 2),
    ("20-F", "Annual report (foreign company)", "The full-year report of a company based outside the US.", "info", 3),
    ("40-F", "Annual report (Canadian company)", "The full-year report of a Canadian company.", "info", 3),
    ("6-K", "Update from a foreign company", "Results or news from a company based outside the US.", "info", 2),
    ("NT 10", "Report will be late", "The company cannot file its report on time. Markets usually read this as a warning sign.", "bad", 3),
    ("NT 20", "Report will be late", "The company cannot file its annual report on time.", "bad", 3),
    ("S-1", "Registered new shares for sale", "An IPO or a new share sale. More shares can dilute existing holders.", "watch", 3),
    ("F-1", "Registered new shares for sale", "An IPO or a new share sale by a foreign company.", "watch", 3),
    ("S-3", "Got ready to sell securities", "A shelf registration: the company can sell shares or debt quickly later.", "watch", 2),
    ("F-3", "Got ready to sell securities", "A shelf registration by a foreign company.", "watch", 2),
    ("424B2", "Sold notes or bonds", "Terms of a debt or structured-note sale. Routine for banks and large borrowers.", "info", 1),
    ("424B", "Selling shares or debt", "The terms of an offering. Share sales dilute existing holders.", "watch", 2),
    ("S-4", "Merger or exchange offer paperwork", "Shares to be issued in a merger or exchange.", "watch", 3),
    ("DEFM14", "Merger vote", "Shareholders are being asked to approve a merger.", "watch", 3),
    ("PREM14", "Merger vote (draft)", "Draft papers for a merger vote.", "watch", 3),
    ("425", "Merger communication", "A message about a pending merger.", "watch", 2),
    ("SC TO", "Tender offer", "Someone is offering to buy shares directly from holders, usually above the market price.", "good", 3),
    ("SC 14D9", "Board response to a tender offer", "The board's recommendation on an offer for the shares.", "watch", 3),
    ("SC 13D", "A large investor took or changed an active stake", "An owner of more than 5% who may push for changes.", "watch", 3),
    ("SCHEDULE 13D", "A large investor took or changed an active stake", "An owner of more than 5% who may push for changes.", "watch", 3),
    ("SC 13G", "A large investor reported a passive stake", "An owner of more than 5%, held as an investment.", "info", 1),
    ("SCHEDULE 13G", "A large investor reported a passive stake", "An owner of more than 5%, held as an investment.", "info", 1),
    ("DEF 14A", "Proxy statement", "The annual meeting agenda: board elections and executive pay.", "info", 1),
    ("DEFA14A", "Extra proxy material", "Additional material for a shareholder vote.", "info", 1),
    ("144", "An insider plans to sell shares", "Notice that an executive, director or large holder intends to sell.", "watch", 1),
    ("4", "Insider bought or sold shares", "An executive, director or 10% owner reported a trade in the company's stock.", "info", 1),
    ("3", "New insider registered", "A new executive, director or 10% owner reported their holdings.", "info", 1),
    ("5", "Annual insider holdings report", "Year-end report of insider holdings.", "info", 1),
    ("15-", "Ending SEC reporting", "The company is deregistering its securities. Public information will stop.", "bad", 3),
    ("25", "A security is leaving an exchange", "A listing is being removed. Often a bond or note that matured; sometimes the stock itself.", "info", 1),
    ("8-K", "Company news", "A current report on an event shareholders should know about.", "info", 2),
    ("SD", "Supply-chain disclosure", "Conflict minerals or resource payment disclosure.", "info", 1),
    ("11-K", "Employee plan report", "Annual report of an employee stock plan.", "info", 1),
    ("EFFECT", "Registration took effect", "A previously filed registration is now effective.", "info", 1),
    ("POS", "Updated a registration", "An amendment to an earlier registration.", "info", 1),
    ("ARS", "Annual report to shareholders", "The glossy annual report.", "info", 1),
]
SKIP = ("13F", "N-", "NPORT", "CORRESP", "UPLOAD", "497", "485", "FWP", "ABS", "10-D", "TA-", "X-17", "D", "D/A",
        "MA", "CERT", "NO ACT", "24F", "40-17", "SEC STAFF", "IRANNOTICE", "PX14", "305B2", "8-A", "RW", "AW", "DRS")


def label(form: str, items: str = "") -> dict | None:
    f = form.upper().strip()
    if f in SKIP or any(f.startswith(s) for s in SKIP if s not in ("D", "D/A", "MA")):
        return None
    if f.startswith("8-K"):
        codes = [c for c in (items or "").split(",") if c in ITEMS]
        if codes:
            best = max(codes, key=lambda c: (ITEMS[c][3], -codes.index(c)))
            title, why, tone, imp = ITEMS[best]
            others = [ITEMS[c][0].lower() for c in codes if c != best]
            if others:
                title += "; also " + ", ".join(others[:2])
            return dict(title=title, why=why, tone=tone, importance=imp)
    for prefix, title, why, tone, imp in FORMS:
        if f == prefix or f.startswith(prefix) and (len(prefix) > 1 or f in ("4", "4/A", "3", "3/A", "5", "5/A")):
            if f.endswith("/A"):
                title += " (amended)"
            return dict(title=title, why=why, tone=tone, importance=imp)
    return None


def _get(client: httpx.Client, url: str) -> httpx.Response:
    for attempt in range(3):
        r = client.get(url)
        if r.status_code in (403, 429):            # blocked or throttled: back off, never work around it
            time.sleep(10 * (attempt + 1))
            continue
        return r
    r.raise_for_status()
    return r


def daily_index(client: httpx.Client, day: pd.Timestamp) -> pd.DataFrame | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"form.{day:%Y%m%d}.parquet"
    if f.exists():
        return pd.read_parquet(f)
    q = (day.month - 1) // 3 + 1
    r = _get(client, f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx")
    time.sleep(0.25)
    if r.status_code != 200:                       # holiday, weekend or not published yet
        return None
    rows = [m.groups() for m in map(LINE.match, r.text.splitlines()) if m]
    df = pd.DataFrame(rows, columns=["form", "company", "cik", "date", "path"])
    df["cik"] = df["cik"].astype(int)
    df["date"] = pd.to_datetime(df["date"])
    df.to_parquet(f, index=False)
    return df


def _take(rec: dict, known: dict[str, str]) -> None:
    for acc, form, items in zip(rec["accessionNumber"], rec["form"], rec["items"]):
        if form.startswith("8-K"):
            known[acc] = items


def eight_k_items(client: httpx.Client, want: pd.DataFrame) -> dict[str, str]:
    """accession number -> 8-K item codes. History comes from the SEC bulk submissions file on disk
    (no requests); only filings newer than that file are looked up on the live submissions feed."""
    cache = DATA_DIR / "sec" / "items.json"
    known: dict[str, str] = json.loads(cache.read_text()) if cache.exists() else {}
    want = want[~want["accession"].isin(known)]
    bulk = DATA_DIR / "sec" / "submissions.zip"
    if bulk.exists() and len(want):
        with zipfile.ZipFile(bulk) as z:
            names = set(z.namelist())
            for cik in sorted(set(want["cik"])):
                name = f"CIK{cik:010d}.json"
                if name in names:
                    _take(json.loads(z.read(name))["filings"]["recent"], known)
        want = want[~want["accession"].isin(known)]
    ciks = sorted(set(want["cik"]))
    print(f"  8-K details: {len(ciks):,} companies to look up on the live feed", flush=True)

    def one(cik: int):
        r = _get(client, f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
        time.sleep(0.5)                            # 4 workers x 2 a second: under the SEC's 10 a second
        return r.json()["filings"]["recent"] if r.status_code == 200 else None

    with ThreadPoolExecutor(4) as pool:
        for rec in pool.map(one, ciks):
            if rec:
                _take(rec, known)
    cache.write_text(json.dumps(known))
    return known


def build(days: int = 45) -> pd.DataFrame:
    listed = pd.read_parquet(DATA_DIR / "listed.parquet")[["ticker", "cik"]]
    old = pd.read_parquet(OUT) if OUT.exists() else None
    end = pd.Timestamp.now("America/New_York").tz_localize(None).normalize()
    span = pd.bdate_range(end - pd.Timedelta(days=days), end)
    frames = []
    with httpx.Client(timeout=60, headers=UA) as client:
        published = set()                          # the days EDGAR has an index for (no holidays, not today yet)
        for y, q in sorted({(d.year, (d.month - 1) // 3 + 1) for d in span}):
            r = _get(client, f"https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/index.json")
            published |= {i["name"] for i in r.json()["directory"]["item"]}
            time.sleep(0.25)
        for day in span:
            if f"form.{day:%Y%m%d}.idx" not in published:
                continue
            df = daily_index(client, day)
            if df is not None:
                frames.append(df)
        f = pd.concat(frames, ignore_index=True).merge(listed, on="cik")
        f["accession"] = f["path"].str.extract(r"/([\d-]+)\.txt$")[0]
        is8k = f["form"].str.startswith("8-K")
        have = set(old.loc[old["form"].str.startswith("8-K") & (old["items"] != ""), "accession"]) if old is not None else set()
        print(f"filings: {len(f):,} by listed companies over {len(frames)} days", flush=True)
        items = eight_k_items(client, f.loc[is8k & ~f["accession"].isin(have), ["cik", "accession"]])
    f["items"] = f["accession"].map(items).fillna("")
    if old is not None:
        f["items"] = f["items"].where(f["items"] != "", f["accession"].map(old.drop_duplicates("accession").set_index("accession")["items"]).fillna(""))
    lab = [label(a, b) for a, b in zip(f["form"], f["items"])]
    f = f[[x is not None for x in lab]].copy()
    f = pd.concat([f.reset_index(drop=True), pd.DataFrame([x for x in lab if x])], axis=1)
    acc = f["accession"].str.replace("-", "", regex=False)
    f["url"] = "https://www.sec.gov/Archives/edgar/data/" + f["cik"].astype(str) + "/" + acc + "/" + f["accession"] + "-index.htm"
    f = f[["date", "ticker", "cik", "form", "items", "title", "why", "tone", "importance", "url", "accession"]]
    if old is not None:
        f = pd.concat([old[old["date"] < span[0]], f], ignore_index=True)
    f = f.drop_duplicates(["ticker", "accession"]).sort_values(["date", "ticker"]).reset_index(drop=True)
    f.to_parquet(OUT, index=False)
    return f


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45)
    a = ap.parse_args()
    f = build(a.days)
    print(f"filings: {len(f):,} rows, {f['ticker'].nunique():,} tickers, {f['date'].min():%Y-%m-%d} -> {f['date'].max():%Y-%m-%d}")
