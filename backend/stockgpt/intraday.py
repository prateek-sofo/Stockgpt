"""The two-minute loop: every new piece of company news gets a timestamp, a live price, and a test.

Each cycle (default every 120 seconds):
  1. pull what is new: Sofo World's news and events when it is connected (SOFO_WORLD_URL), and the
     SEC's real-time feed of 8-K filings, which needs no key and is always on;
  2. for every item about a listed company, record an alert: when it was published, when we saw it,
     the live price at that moment, and what each simple rule would do;
  3. come back to every open alert 30 and 60 minutes later and at the close, and write down what the
     stock did, against the S&P 500 over the same minutes.

Nothing here assumes a rule works. The rules are hypotheses, and the scorecard is the evidence:
  first_move   trade in the direction the stock has already moved today when the news is seen
  tone         trade in the direction of the news itself (good news up, bad news down)
  model        trade the side the morning scan gave this stock, if it gave one

    python -m stockgpt.intraday once        # one cycle
    python -m stockgpt.intraday loop        # every two minutes until stopped
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import time
from datetime import datetime, timezone

import httpx
import pandas as pd

from . import live
from .config import ARTIFACT_DIR, DATA_DIR
from .sources.filings import UA, label

DIR = ARTIFACT_DIR / "intraday"
FEED = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include&count=100&output=atom"
HORIZONS = (("m30", 30), ("m60", 60))
ET = "America/New_York"
COST_BP = 10.0                                         # round trip, for a trade made minutes after news


def _now() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(timezone.utc))


def _state() -> dict:
    p = DIR / "state.json"
    return json.loads(p.read_text()) if p.exists() else {"seen": [], "started": _now().isoformat()}


def _alerts() -> list[dict]:
    p = DIR / "alerts.json"
    return json.loads(p.read_text()) if p.exists() else []


def _save(alerts: list[dict], state: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    state["seen"] = state["seen"][-20000:]
    (DIR / "alerts.json").write_text(json.dumps(alerts[-3000:], separators=(",", ":")))
    (DIR / "state.json").write_text(json.dumps(state))


def edgar_items() -> list[dict]:
    """The newest 8-K filings, as EDGAR publishes them: one request."""
    r = httpx.get(FEED, headers=UA, timeout=30)
    if r.status_code != 200:
        return []
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", r.text, flags=re.S):
        acc = re.search(r"accession-number=([\d-]+)", e)
        cik = re.search(r"\((\d{10})\)", e)
        when = re.search(r"<updated>([^<]+)</updated>", e)
        form = re.search(r'term="([^"]+)"', e)
        if not (acc and cik and when and form):
            continue
        items = ",".join(re.findall(r"Item (\d\.\d\d)", html.unescape(e)))
        link = re.search(r'href="([^"]+)"', e)
        out.append(dict(id="sec:" + acc.group(1), cik=int(cik.group(1)), published_at=pd.Timestamp(when.group(1)).tz_convert("UTC").isoformat(),
                        form=form.group(1), items=items, url=link.group(1) if link else "", source="SEC filing"))
    return out


def sofo_items() -> list[dict]:
    """Articles Sofo World has seen since our last pull (only when it is connected)."""
    if not os.environ.get("SOFO_WORLD_URL"):
        return []
    from .sources import sofo_api
    try:
        with sofo_api.client() as c:
            added = sofo_api.sync(c, "news")
    except SystemExit:
        return []
    if not added:
        return []
    rows = sofo_api.load("news").tail(added)
    out = []
    for r in rows.itertuples():
        for m in (r.mentions or []):
            if m.get("ticker") and (m.get("is_subject") or (m.get("salience") or 0) >= 0.5):
                out.append(dict(id=f"sofo:{r.article_id}:{m['ticker']}", ticker=m["ticker"], published_at=r.published_at, headline=r.title,
                                url=r.url, source=r.publisher or "Sofo World", first_seen=r.first_seen))
    return out


def poll(brief: dict | None = None) -> int:
    """One pull: turn new items into alerts with a live price. Returns the number of new alerts."""
    state, alerts = _state(), _alerts()
    seen = set(state["seen"])
    listed = pd.read_parquet(DATA_DIR / "listed.parquet")
    by_cik = listed.drop_duplicates("cik").set_index("cik")
    names = dict(zip(listed["ticker"], listed["name"]))
    ideas = {i["ticker"]: i["action"] for i in (brief or {}).get("longs", []) + (brief or {}).get("shorts", [])}
    first_run = not state["seen"]
    fresh = []
    for it in edgar_items() + sofo_items():
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        state["seen"].append(it["id"])
        if first_run:
            continue                                   # the backlog at start-up is not news we saw arrive
        if "ticker" not in it:
            if it["cik"] not in by_cik.index:
                continue
            it["ticker"] = by_cik.at[it["cik"], "ticker"]
            lab = label(it["form"], it["items"])
            if lab is None or lab["importance"] < 2:
                continue
            it.update(headline=lab["title"], tone=lab["tone"], why=lab["why"])
        else:
            from .nlp.sentiment import LexiconScorer
            s = float(LexiconScorer().score([it["headline"]])["sentiment"].iloc[0])
            it["tone"] = "good" if s > 0.15 else "bad" if s < -0.15 else "info"
        fresh.append(it)
    if fresh:
        q = live.quotes([f["ticker"] for f in fresh] + ["SPY"])
        spy = q.get("SPY", {})
        now = _now()
        for it in fresh:
            x = q.get(it["ticker"])
            if not x or x.get("price") is None:
                continue
            move = x.get("chg_day")
            rules = {}
            if move is not None and abs(move) >= 0.003:
                rules["first_move"] = 1 if move > 0 else -1
            if it.get("tone") in ("good", "bad"):
                rules["tone"] = 1 if it["tone"] == "good" else -1
            if it["ticker"] in ideas:
                rules["model"] = 1 if ideas[it["ticker"]] == "BUY" else -1
            alerts.append(dict(id=it["id"], ticker=it["ticker"], name=names.get(it["ticker"], ""), headline=it["headline"], why=it.get("why", ""),
                               tone=it.get("tone", "info"), source=it["source"], url=it.get("url", ""), published_at=it["published_at"],
                               seen_at=now.isoformat(), lag_s=int((now - pd.Timestamp(it["published_at"])).total_seconds()),
                               state=x["state"], price=x["price"], chg_day=move, chg_open=x.get("chg_open"), spy=spy.get("price"),
                               rules=rules, marks={}))
    _save(alerts, state)
    return len(fresh)


def mark() -> int:
    """Fill in what each open alert's stock did since the alert: after 30 and 60 minutes (only while
    the market is open) and at the close."""
    state, alerts = _state(), _alerts()
    now = _now()
    due = []
    for a in alerts:
        age = (now - pd.Timestamp(a["seen_at"])).total_seconds() / 60
        if a.get("done") or age > 60 * 24 * 4:
            a["done"] = True
            continue
        want = [k for k, m in HORIZONS if k not in a["marks"] and age >= m and a["state"] == "open" and age < m + 20]
        if "close" not in a["marks"] and age >= 5:
            want.append("close")
        if want:
            due.append((a, want))
    if not due:
        _save(alerts, state)
        return 0
    q = live.quotes(list({a["ticker"] for a, _ in due}) + ["SPY"])
    spy = q.get("SPY", {})
    n = 0
    for a, want in due:
        x = q.get(a["ticker"])
        if not x or x.get("price") is None or not a.get("price"):
            continue
        ret = x["price"] / a["price"] - 1
        mkt = (spy["price"] / a["spy"] - 1) if spy.get("price") and a.get("spy") else 0.0
        for k in want:
            if k == "close":
                # the close mark needs a finished session after the alert
                closed_after = x["state"] == "closed" and x.get("time") and pd.Timestamp(x["time"]) > pd.Timestamp(a["seen_at"])
                if not closed_after:
                    continue
                a["done"] = True
            a["marks"][k] = dict(time=now.isoformat(), price=x["price"], ret=ret, rel=ret - mkt)
            n += 1
    _save(alerts, state)
    return n


def scorecard(alerts: list[dict] | None = None) -> dict:
    alerts = alerts if alerts is not None else _alerts()
    rows = []
    for rule in ("first_move", "tone", "model"):
        for k in ("m30", "m60", "close"):
            xs = [a["rules"][rule] * a["marks"][k]["rel"] for a in alerts if rule in a["rules"] and k in a["marks"]]
            if xs:
                s = pd.Series(xs)
                rows.append(dict(rule=rule, horizon=k, n=len(xs), mean_bp=float(s.mean() * 1e4), net_bp=float(s.mean() * 1e4 - COST_BP),
                                 win=float((s > 0).mean()), t=float(s.mean() / s.std() * len(s) ** 0.5) if len(s) > 2 and s.std() > 0 else None))
    lag = pd.Series([a["lag_s"] for a in alerts if a.get("lag_s") is not None])
    return dict(rows=rows, alerts=len(alerts), median_lag_s=float(lag.median()) if len(lag) else None)


def publish() -> dict:
    alerts, state = _alerts(), _state()
    out = dict(generated_at=_now().isoformat(), started=state.get("started"), connected=dict(sofo=bool(os.environ.get("SOFO_WORLD_URL")), sec=True),
               interval_s=int(os.environ.get("STOCKGPT_INTRADAY_SECONDS", "120")), scorecard=scorecard(alerts), alerts=alerts[-200:][::-1],
               study=json.loads((ARTIFACT_DIR / "event_study.json").read_text()) if (ARTIFACT_DIR / "event_study.json").exists() else None)
    (ARTIFACT_DIR / "intraday.json").write_text(json.dumps(out, separators=(",", ":"), default=str))
    return out


def cycle() -> tuple[int, int]:
    brief = json.loads((ARTIFACT_DIR / "brief.json").read_text()) if (ARTIFACT_DIR / "brief.json").exists() else {}
    new, marked = poll(brief), mark()
    publish()
    return new, marked


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["once", "loop"])
    a = ap.parse_args()
    every = int(os.environ.get("STOCKGPT_INTRADAY_SECONDS", "120"))
    while True:
        try:
            new, marked = cycle()
            print(f"{_now():%H:%M:%S} UTC  new alerts {new}  marks {marked}", flush=True)
        except Exception as ex:                         # one bad cycle must not stop the loop
            print(f"{_now():%H:%M:%S} UTC  cycle failed: {ex}", flush=True)
        if a.cmd == "once":
            break
        time.sleep(every)
