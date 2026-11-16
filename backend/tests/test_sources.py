"""SEC parsing: quarterly extraction, derived Q4, sectors, earnings timing (fixtures only)."""
import json
import zipfile

import pandas as pd

from stockgpt.sources import sec


def test_quarterly_derives_q4_from_annual():
    units = [
        dict(start="2025-01-01", end="2025-03-31", val=10, filed="2025-04-30", form="10-Q"),
        dict(start="2025-04-01", end="2025-06-30", val=11, filed="2025-07-30", form="10-Q"),
        dict(start="2025-07-01", end="2025-09-30", val=12, filed="2025-10-30", form="10-Q"),
        dict(start="2025-01-01", end="2025-12-31", val=50, filed="2026-02-20", form="10-K"),
        dict(start="2025-01-01", end="2025-06-30", val=21, filed="2025-07-30", form="10-Q"),  # YTD: ignored
    ]
    q = {r["end"]: r for r in sec._quarterly(units, duration=True)}
    assert q["2025-12-31"]["val"] == 17 and q["2025-12-31"]["filed"] == "2026-02-20"
    assert len(q) == 4


def test_sic_sector():
    assert sec.sic_sector("3571") == "Technology"
    assert sec.sic_sector("6022") == "Financials"
    assert sec.sic_sector("2834") == "Healthcare"
    assert sec.sic_sector("6798") == "Real Estate"
    assert sec.sic_sector("") == "Other"


def test_earnings_timing_from_acceptance(tmp_path):
    sub = {"sic": "3571", "sicDescription": "Computers", "filings": {"recent": {
        "form": ["8-K", "8-K", "10-Q", "8-K"],
        "items": ["2.02,9.01", "2.02", "", "5.02"],
        "acceptanceDateTime": ["2026-01-29T16:30:12.000Z",   # 11:30 ET -> intraday -> AMC
                               "2026-04-30T11:05:00.000Z",   # 07:05 ET -> BMO
                               "2026-05-01T20:00:00.000Z",
                               "2026-06-01T20:00:00.000Z"]}}}
    z = tmp_path / "submissions.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("CIK0000000042.json", json.dumps(sub))
    e = sec.earnings_dates({42}, z).sort_values("report_date")
    assert e["timing"].tolist() == ["AMC", "BMO"]
    assert e["report_date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-01-29", "2026-04-30"]
    assert sec.earnings_dates.sic[42] == ("3571", "Computers")


def test_fundamentals_point_in_time(tmp_path):
    def q(y, m_end, val, filed):
        start = pd.Timestamp(m_end) - pd.Timedelta(days=89)
        return dict(start=str(start.date()), end=m_end, val=val, filed=filed, form="10-Q")
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [q(2024, "2024-03-31", 100, "2024-05-01"),
                                        q(2025, "2025-03-31", 120, "2025-05-02")]}},
        "OperatingIncomeLoss": {"units": {"USD": [q(2025, "2025-03-31", 30, "2025-05-02")]}},
    }}}
    z = tmp_path / "companyfacts.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("CIK0000000042.json", json.dumps(facts))
    f = sec.fundamentals({42}, z).sort_values("period_end")
    last = f.iloc[-1]
    assert pd.Timestamp(last["report_date"]) == pd.Timestamp("2025-05-02")   # keyed by filing date
    assert abs(last["revenue_growth_yoy"] - 0.2) < 1e-9
    assert abs(last["operating_margin"] - 0.25) < 1e-9
