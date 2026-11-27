"""The daily record: what the model said before the open, and what happened by the close.

  issue    freeze today's predictions the moment they are made. Once the session has opened the
           entry can no longer be changed, so the record cannot be improved after the fact.
  capture  note live option quotes for the suggested contracts (near the open, near the close).
  settle   after the close: each stock's move from open to close, the average tradable stock's
           move, whether each call was right, and the day's result. Costs are included.

Entries live in artifacts/journal/<date>.json and are copied to published/journal/ so they are
committed to the repository: the commit time is independent proof of when a prediction was made.

    python -m stockgpt.journal issue | capture | settle | index
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import ARTIFACT_DIR, DATA_DIR, ROOT, SETTINGS

DIR = ARTIFACT_DIR / "journal"
PUBLISHED = ROOT / "published" / "journal"
ET = "America/New_York"
KEEP = ["ticker", "name", "sector", "action", "strength", "score_pct", "exp_move_pct", "ref_price", "shares", "notional",
        "why", "headlines", "option", "earnings_today"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _open_time(day: str) -> pd.Timestamp:
    return pd.Timestamp(f"{day} 09:30", tz=ET)


def _load(day: str) -> dict | None:
    p = DIR / f"{day}.json"
    return json.loads(p.read_text()) if p.exists() else None


def _save(entry: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / f"{entry['date']}.json").write_text(json.dumps(entry, separators=(",", ":"), default=str))


def issue(brief: dict | None = None, issued_at: str | None = None) -> dict:
    """Record the scan's ideas for its session. Before the open a newer scan replaces the entry (and
    the earlier time is kept in `revised`); from the open on, the entry is frozen."""
    brief = brief or json.loads((ARTIFACT_DIR / "brief.json").read_text())
    day = brief["scan_date"]
    at = pd.Timestamp(issued_at) if issued_at else pd.Timestamp(_now())
    old = _load(day)
    if old and (old.get("frozen") or at >= _open_time(day)):
        if old and not old.get("frozen"):
            old["frozen"] = True
            _save(old)
        return old
    if at >= _open_time(day):
        # a scan made after the open is not a prediction for that session: keep it out of the record
        raise SystemExit(f"{day}: the session opened before this scan was made; not recorded")
    entry = dict(date=day, issued_at=at.isoformat(), frozen=False, status="open",
                 revised=([*old.get("revised", []), old["issued_at"]] if old else []),
                 ideas=[{k: i.get(k) for k in KEEP} for i in brief.get("longs", []) + brief.get("shorts", [])],
                 option_expiry=brief.get("option_expiry"), universe=brief.get("universe", []),
                 equity=brief.get("equity"), per_position=(brief.get("book") or {}).get("per_position"),
                 data_used=brief.get("data_used", []), captures=[], result=None)
    _save(entry)
    return entry


def capture(day: str | None = None) -> dict | None:
    """Add a timestamped set of live quotes (stock, and the suggested option contract) to today's entry."""
    from . import live
    day = day or str(pd.Timestamp.now(ET).date())
    entry = _load(day)
    if not entry:
        return None
    if pd.Timestamp(_now()) >= _open_time(day):
        entry["frozen"] = True
    brief = dict(scan_date=day, option_expiry=entry.get("option_expiry"), longs=[i for i in entry["ideas"] if i["action"] == "BUY"],
                 shorts=[i for i in entry["ideas"] if i["action"] == "SHORT"])
    b = live.board(brief, with_options=True)
    snap = dict(time=b["as_of"], state=b["state"], spy=b["market"].get("price"),
                quotes={r["ticker"]: dict(price=(r["quote"] or {}).get("price"), chg_open=(r["quote"] or {}).get("chg_open"),
                                          option=({k: r["option"].get(k) for k in ("contract", "strike", "expiry", "right", "bid", "ask", "mid", "iv")}
                                                  if r["option"] else None)) for r in b["rows"]})
    priced = sum(1 for q in snap["quotes"].values() if q.get("price") is not None)
    if priced < 0.8 * len(snap["quotes"]):
        return None                                # the quote service was throttling: an incomplete check is not kept
    entry["captures"].append(snap)
    _save(entry)
    return snap


def settle(day: str, prices: pd.DataFrame) -> dict | None:
    """Fill in what happened, from that day's open and close."""
    entry = _load(day)
    if not entry or entry.get("status") == "settled":
        return entry
    px = prices[prices["date"] == pd.Timestamp(day)].dropna(subset=["open", "close"]).set_index("ticker")
    if not len(px):
        return entry                                   # the session's prices are not in yet
    move = px["close"] / px["open"] - 1
    uni = [t for t in entry.get("universe", []) if t in move.index]
    market = float(move.reindex(uni).mean()) if uni else float(move.median())
    cost = 2 * SETTINGS.costs.stock_slippage_bps / 1e4   # in and out
    rows, pnl = [], 0.0
    for i in entry["ideas"]:
        t, sgn = i["ticker"], (1 if i["action"] == "BUY" else -1)
        if t not in move.index:
            i["outcome"] = None
            continue
        m = float(move[t])
        rel = m - market
        ret = sgn * m - cost
        i["outcome"] = dict(open=float(px.at[t, "open"]), close=float(px.at[t, "close"]), stock_move=m, rel_move=rel,
                            correct=bool(rel * sgn > 0), trade_ret=ret, pnl=round((i.get("notional") or 0) * ret, 2))
        pnl += i["outcome"]["pnl"]
        rows.append((i["action"], m, rel * sgn > 0))
    # options: only where live quotes were captured near the open and near the close
    caps = [c for c in entry.get("captures", []) if c.get("state") == "open"]
    if len(caps) >= 2:
        first, last = caps[0], caps[-1]
        for i in entry["ideas"]:
            a, b = (first["quotes"].get(i["ticker"]) or {}).get("option"), (last["quotes"].get(i["ticker"]) or {}).get("option")
            if a and b and a.get("ask") and b.get("bid") and a.get("contract") == b.get("contract"):
                i["option_outcome"] = dict(contract=a["contract"], bought_at=a["ask"], sold_at=b["bid"], ret=b["bid"] / a["ask"] - 1,
                                           from_time=first["time"], to_time=last["time"])
    longs = [m for a, m, _ in rows if a == "BUY"]
    shorts = [m for a, m, _ in rows if a == "SHORT"]
    spy = float(move["SPY"]) if "SPY" in move.index else None
    entry["result"] = dict(ideas=len(rows), right=int(sum(c for *_, c in rows)), hit_rate=(sum(c for *_, c in rows) / len(rows)) if rows else None,
                           long_move=float(pd.Series(longs).mean()) if longs else None,
                           short_move=float(pd.Series(shorts).mean()) if shorts else None,
                           spread=(float(pd.Series(longs).mean()) - float(pd.Series(shorts).mean())) if longs and shorts else None,
                           market_move=market, spy_move=spy, pnl=round(pnl, 2),
                           ret_on_equity=(pnl / entry["equity"]) if entry.get("equity") else None,
                           settled_at=_now().isoformat())
    entry["status"], entry["frozen"] = "settled", True
    _save(entry)
    return entry


def settle_all() -> list[str]:
    src = DATA_DIR / "prices_all.parquet"
    if not src.exists():
        return []
    prices = pd.read_parquet(src, columns=["date", "ticker", "open", "close"])
    prices["date"] = pd.to_datetime(prices["date"])
    done = []
    for p in sorted(DIR.glob("*.json")):
        e = json.loads(p.read_text())
        # a session is over once its date is behind us in New York, or it is past 16:15 there today
        now = pd.Timestamp.now(ET)
        over = pd.Timestamp(e["date"]).date() < now.date() or (pd.Timestamp(e["date"]).date() == now.date() and now.hour * 60 + now.minute >= 16 * 60 + 15)
        if e.get("status") != "settled" and over and (settle(e["date"], prices) or {}).get("status") == "settled":
            done.append(e["date"])
    return done


def index() -> dict:
    """journal.json: every day in the record, newest first, and the running tally."""
    days = [json.loads(p.read_text()) for p in sorted(DIR.glob("*.json"))] if DIR.exists() else []
    settled = [d for d in days if d.get("status") == "settled"]
    ideas, right = sum(d["result"]["ideas"] for d in settled), sum(d["result"]["right"] for d in settled)
    eq, curve = 1.0, []
    for d in settled:
        eq *= 1 + (d["result"]["ret_on_equity"] or 0)
        curve.append(dict(date=d["date"], equity=eq, hit_rate=d["result"]["hit_rate"]))
    out = dict(days=[dict(date=d["date"], issued_at=d["issued_at"], status=d["status"], ideas=len(d["ideas"]), result=d.get("result"),
                          captures=len(d.get("captures", []))) for d in reversed(days)],
               tally=dict(days=len(settled), ideas=ideas, right=right, hit_rate=(right / ideas) if ideas else None,
                          pnl=round(sum(d["result"]["pnl"] for d in settled), 2), total_return=eq - 1, since=days[0]["date"] if days else None),
               curve=curve, generated_at=_now().isoformat())
    (ARTIFACT_DIR / "journal.json").write_text(json.dumps(out, separators=(",", ":")))
    if DIR.exists():                                   # the committed copy
        PUBLISHED.mkdir(parents=True, exist_ok=True)
        for p in DIR.glob("*.json"):
            shutil.copy(p, PUBLISHED / p.name)
    return out


def restore() -> None:
    """On a fresh machine, start from the committed record."""
    if PUBLISHED.exists():
        DIR.mkdir(parents=True, exist_ok=True)
        for p in PUBLISHED.glob("*.json"):
            if not (DIR / p.name).exists():
                shutil.copy(p, DIR / p.name)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["issue", "capture", "settle", "index"])
    a = ap.parse_args()
    restore()
    if a.cmd == "issue":
        e = issue()
        print(f"journal: {e['date']} issued {e['issued_at']} ({len(e['ideas'])} ideas, frozen={e['frozen']})")
    elif a.cmd == "capture":
        s = capture()
        print("journal: captured live quotes at", s["time"] if s else "(no entry for today)")
    elif a.cmd == "settle":
        print("journal: settled", settle_all() or "nothing new")
    t = index()["tally"]
    print(f"record: {t['days']} settled days, {t['right']}/{t['ideas']} calls right")
