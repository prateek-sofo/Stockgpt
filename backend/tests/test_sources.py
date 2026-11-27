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


def test_filing_labels_are_plain_and_ranked():
    from stockgpt.sources.filings import label
    e = label("8-K", "2.02,9.01")
    assert e["title"] == "Reported financial results" and e["importance"] == 3
    # the most important item leads; the rest are mentioned
    mixed = label("8-K", "7.01,4.02,9.01")
    assert mixed["title"].startswith("Said past financial statements cannot be relied on") and mixed["tone"] == "bad"
    assert label("NT 10-Q")["tone"] == "bad"
    assert label("4")["importance"] == 1 and label("424B2")["importance"] == 1
    assert label("10-K/A")["title"].endswith("(amended)")
    # fund and manager paperwork is not company news
    assert label("13F-HR") is None and label("N-CSR") is None and label("D") is None


def _sofo_obs():
    """Observations shaped like Sofo World's SEC crawler output (FN01 metric points, filing records)."""
    rows = []
    def metric(name, start, end, val, filed, form="10-Q"):
        rows.append(dict(entity_ref={"cik": "0000000042"}, field=f"metric.{name}", source_id="FN01", published_at=filed,
                         value=json.dumps(dict(metric=name, value=val, start=start, end=end, period="Q", form=form, filed=filed))))
    q = [("2024-01-01", "2024-03-31", "2024-05-02"), ("2024-04-01", "2024-06-30", "2024-08-01"),
         ("2024-07-01", "2024-09-30", "2024-11-01"), ("2025-01-01", "2025-03-31", "2025-05-01")]
    for i, (s, e, f) in enumerate(q):
        metric("revenue", s, e, 100.0 + 10 * i, f)
        metric("operating_income", s, e, 20.0 + i, f)
    metric("revenue", "2024-01-01", "2024-12-31", 480.0, "2025-02-10", "10-K")      # FY: Q4 is derived
    rows.append(dict(entity_ref={"cik": "42"}, field="filing", source_id="ID04", published_at="2025-05-01",
                     value=dict(form="8-K", filed="2025-05-01", accession="0000000042-25-000007", items=["2.02", "9.01"],
                                url="https://www.sec.gov/x", accepted="2025-05-01T20:05:00.000Z")))
    rows.append(dict(entity_ref={"cik": "42"}, field="filing", source_id="ID04", published_at="2025-05-03",
                     value=dict(form="13F-HR", filed="2025-05-03", accession="0000000042-25-000008", items=[])))
    return pd.DataFrame(rows)


def test_sofo_world_financials_and_filings():
    from stockgpt.sources.sofo_finance import build
    listed = pd.DataFrame([dict(ticker="acme", name="Acme", exchange="NYSE", cik=42, sic="3674")])
    out = build(_sofo_obs(), listed)
    f = out["fundamentals"].set_index("period_end")
    # Q4 2024 = FY 480 - (100 + 110 + 120), dated by the 10-K, not earlier
    assert f.loc["2024-12-31", "revenue"] == 150.0 and str(f.loc["2024-12-31", "report_date"].date()) == "2025-02-10"
    # Q1 2025 vs Q1 2024: 130 / 100 - 1
    assert abs(f.loc["2025-03-31", "revenue_growth_yoy"] - 0.30) < 1e-9 and f["ticker"].eq("ACME").all()
    assert out["coverage"]["metrics_missing"] == ["gross_profit", "net_income", "eps", "cfo", "capex", "cash", "debt", "shares"]
    assert list(out["filings"]["title"]) == ["Reported financial results"]            # the 13F is not company news
    e = out["earnings"].iloc[0]
    assert e["timing"] == "AMC" and bool(e["timing_known"]) and str(e["report_date"].date()) == "2025-05-01"


def test_sofo_api_sync_is_incremental_and_point_in_time(tmp_path, monkeypatch):
    """Pages are pulled from the stored cursor; an article found late is never dated before Sofo World had it."""
    import httpx
    from stockgpt.sources import sofo_api
    monkeypatch.setattr(sofo_api, "STORE", tmp_path)
    art = lambda i, pub, seen, m: dict(article_id=f"a{i}", first_seen=seen, published_at=pub, title=f"Acme story {i}", snippet="s",
                                       url=f"https://n/{i}", publisher="Wire", is_press_release=False, cluster_id=None, mentions=m)
    acme = [dict(ticker="ACME", cik="42", salience=0.9, is_subject=True)]
    pages = {None: dict(rows=[art(1, "2026-11-17T21:30:00+00:00", "2026-11-17T21:40:00+00:00", acme),
                              art(2, "2026-11-10T12:00:00+00:00", "2026-11-18T09:00:00+00:00", acme)], next="c1", more=True),
             "c1": dict(rows=[art(3, "2026-11-18T11:00:00+00:00", "2026-11-18T11:01:00+00:00",
                                  [dict(ticker=None, cik="42", salience=0.2, is_subject=False)])], next="c2", more=False),
             "c2": dict(rows=[], next=None, more=False)}
    calls = []
    def handler(req):
        assert req.headers["authorization"] == "Bearer sk_world_test" and req.url.path == "/v1/exports/news"
        calls.append(req.url.params.get("after"))
        return httpx.Response(200, json=pages[req.url.params.get("after")])
    c = sofo_api.client("https://sofo.test", "sk_world_test", transport=httpx.MockTransport(handler))
    assert sofo_api.sync(c, "news") == 3 and calls == [None, "c1"]
    assert sofo_api.sync(c, "news") == 0 and calls[-1] == "c2"          # the next run starts where the last ended
    out = sofo_api.news_table(sofo_api.load("news"), pd.DataFrame())
    assert list(out["headline"]) == ["Acme story 2", "Acme story 1"]    # story 3 only mentions Acme in passing
    late = out.set_index("headline").loc["Acme story 2", "published_at"]
    assert str(late) == "2026-11-10 18:00:00+00:00"                     # found 8 days late: capped at publication + 6 h


def test_same_headline_on_another_day_is_another_story():
    from stockgpt.sources.sofo_world import _dedupe
    df = pd.DataFrame(dict(ticker=["ACME"] * 3, headline=["Acme declares quarterly dividend"] * 3, cluster_id=[None] * 3,
                           published_at=["2026-08-01T12:00:00Z", "2026-08-01T15:00:00Z", "2026-11-01T12:00:00Z"]))
    assert len(_dedupe(df)) == 2          # the syndicated copy on Aug 1 counts once; November is a new story


def test_scan_targets_the_session_after_the_last_close():
    from stockgpt.sessions import add_pending_session, next_session
    assert str(next_session("2026-11-25").date()) == "2026-11-27"      # Thanksgiving is skipped
    assert str(next_session("2026-04-02").date()) == "2026-04-06"      # Good Friday and the weekend
    px = pd.DataFrame(dict(date=pd.to_datetime(["2026-11-18", "2026-11-19", "2026-11-19"]), ticker=["A", "A", "B"], close=[1.0, 2.0, 3.0]))
    pre = pd.DataFrame(dict(date=pd.to_datetime(["2026-11-20"]), ticker=["B"], premarket=[3.3]))
    out = add_pending_session(px, pre)
    new = out[out["date"] == "2026-11-20"].set_index("ticker")
    assert list(new.index) == ["A", "B"] and new["close"].isna().all() and new.loc["B", "premarket"] == 3.3
    assert len(add_pending_session(out, pre)) == len(out)               # never added twice


def test_lake_news_is_point_in_time_and_mapped(monkeypatch):
    from stockgpt.sources import sofo_lake

    rows = pd.DataFrame({
        "article_id": ["a", "b", "c"], "cik": ["0000320193", "320193", "999"],
        "headline": ["late find", "on time", "unlisted"], "body": ["s1", "s2", "s3"], "url": ["u1", "u2", "u3"], "source": ["x.com"] * 3,
        "published_at": ["2026-03-02 14:00:00", "2026-03-03 14:00:00", "2026-03-03 14:00:00"],
        "fetched_at": ["2026-03-05 09:00:00", "2026-03-03 14:05:00", "2026-03-03 14:05:00"],
        "salience": ["0.9"] * 3, "role": ["subject"] * 3,
    })
    monkeypatch.setattr(sofo_lake, "query", lambda sql, timeout=0: (rows.copy(), dict(bytes=0, usd=0)))
    sec = pd.DataFrame({"cik": ["320193"], "ticker": ["AAPL"], "name": ["Apple"]})
    out = sofo_lake.news("2026-03-01", sec).set_index("headline")
    assert set(out.index) == {"late find", "on time"}            # unlisted company dropped
    assert (out["ticker"] == "AAPL").all()
    # a story fetched days late is visible no earlier than publish + 6 h, never before it was published
    assert out.loc["late find", "published_at"] == pd.Timestamp("2026-03-02 20:00", tz="UTC")
    assert out.loc["on time", "published_at"] == pd.Timestamp("2026-03-03 14:05", tz="UTC")
