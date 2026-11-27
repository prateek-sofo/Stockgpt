"""Live stock and option prices (Yahoo Finance public endpoints; quotes can lag the tape by up to
15 minutes on some exchanges, and every quote carries its own timestamp).

    quotes(["NVDA", "AAPL"])            last price, today's open, move since the open, session state
    option_chain("NVDA")                expiries and the strikes around the money: bid, ask, IV, volume
    board(brief)                        today's ideas marked against live prices: is each call on track
                                        (stock quotes only; option quotes are fetched one idea at a time)

Answers are cached for a few seconds so a page that refreshes often makes few requests.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
import numpy as np
import pandas as pd

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{}"
UA = {"User-Agent": "StockGPT research"}
QUOTE_TTL, CHAIN_TTL = 30.0, 120.0
_cache: dict[tuple, tuple[float, object]] = {}
_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=15, headers=UA)
    return _client


def _cached(key: tuple, ttl: float, make):
    """Fresh value if we have one; otherwise fetch. If the fetch fails (Yahoo throttles bursts), the
    last value we had is returned instead of nothing: its own timestamp says how old it is."""
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = make()
    if val is not None:
        _cache[key] = (time.time(), val)
        return val
    return hit[1] if hit else None


def _ysym(t: str) -> str:
    return t.upper().replace(".", "-")


def _iso(ts) -> str | None:
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat() if ts else None


def _quote(sym: str) -> dict | None:
    res = None
    for attempt in range(3):
        try:
            r = _http().get(CHART.format(_ysym(sym)), params=dict(interval="5m", range="1d", includePrePost="true"))
            if r.status_code == 200:
                res = r.json()["chart"]["result"][0]
                break
            if r.status_code != 429:
                return None
        except Exception:
            pass
        time.sleep(1.5 * (attempt + 1))                # throttled: wait, then try again
    if res is None:
        return None
    m, ts = res["meta"], res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    reg = m.get("currentTradingPeriod", {}).get("regular", {})
    start, end, now = reg.get("start", 0), reg.get("end", 0), time.time()
    state = "pre" if now < start else "open" if now < end else "closed"
    opens, closes = q.get("open") or [], q.get("close") or []
    open_px = next((o for t, o in zip(ts, opens) if t >= start and o is not None), None)
    last_t, last = next(((t, c) for t, c in zip(reversed(ts), reversed(closes)) if c is not None), (None, None))
    price = m.get("regularMarketPrice") if state != "pre" else (last or m.get("regularMarketPrice"))
    stamp = m.get("regularMarketTime") if state != "pre" else last_t
    prev = m.get("previousClose") or m.get("chartPreviousClose")
    return dict(ticker=sym.upper(), price=price, prev_close=prev, open=open_px, high=m.get("regularMarketDayHigh"),
                low=m.get("regularMarketDayLow"), volume=m.get("regularMarketVolume"), state=state, time=_iso(stamp),
                chg_day=(price / prev - 1) if price and prev else None,
                chg_open=(price / open_px - 1) if price and open_px else None)


def quotes(symbols: list[str]) -> dict[str, dict]:
    syms = list(dict.fromkeys(s.upper() for s in symbols if s))[:60]
    with ThreadPoolExecutor(2) as pool:
        out = list(pool.map(lambda s: _cached(("q", s), QUOTE_TTL, lambda: _quote(s)), syms))
    return {s: q for s, q in zip(syms, out) if q}


def _rows(df: pd.DataFrame, spot: float | None, around: int) -> list[dict]:
    if spot:
        i = int((df["strike"] - spot).abs().values.argmin())
        df = df.iloc[max(0, i - around): i + around + 1]
    def f(v):
        return None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)
    out = []
    for r in df.itertuples():
        bid, ask = f(r.bid), f(r.ask)
        mid = (bid + ask) / 2 if bid and ask else f(r.lastPrice)
        iv = f(r.impliedVolatility)
        out.append(dict(contract=r.contractSymbol, strike=float(r.strike), bid=bid, ask=ask, mid=mid, last=f(r.lastPrice),
                        iv=iv if iv and iv > 0.01 else None,          # Yahoo reports 0 when it cannot solve for IV
                        volume=int(r.volume) if pd.notna(r.volume) else 0,
                        open_interest=int(r.openInterest) if pd.notna(r.openInterest) else 0,
                        spread_pct=((ask - bid) / mid) if bid and ask and mid else None,
                        last_trade=r.lastTradeDate.isoformat() if pd.notna(r.lastTradeDate) else None,
                        in_the_money=bool(r.inTheMoney)))
    return out


def option_chain(sym: str, expiry: str | None = None, around: int = 8) -> dict | None:
    """Calls and puts around the money for one expiry (default: the nearest), plus the list of expiries."""
    def make():
        import yfinance as yf
        try:
            t = yf.Ticker(_ysym(sym))
            exps = list(t.options)
            if not exps:
                return None
            exp = expiry if expiry in exps else next((e for e in exps if expiry and e >= expiry), exps[0])
            ch = t.option_chain(exp)
        except Exception:
            return None
        spot = (ch.underlying or {}).get("regularMarketPrice") if hasattr(ch, "underlying") else None
        return dict(ticker=sym.upper(), expiry=exp, expiries=exps[:12], spot=spot, time=datetime.now(timezone.utc).isoformat(),
                    calls=_rows(ch.calls, spot, around), puts=_rows(ch.puts, spot, around))
    return _cached(("c", sym.upper(), expiry), CHAIN_TTL, make)


def option_quote(sym: str, expiry: str, strike: float | None, right: str) -> dict | None:
    """The listed contract closest to a suggested one: same side, nearest expiry on or after, nearest
    strike. With no strike given: the contract closest to the current price (at the money)."""
    ch = option_chain(sym, expiry, around=40)
    if not ch:
        return None
    side = ch["calls"] if right.upper().startswith("C") else ch["puts"]
    target = strike if strike is not None else ch["spot"]
    if not side or target is None:
        return None
    best = min(side, key=lambda r: abs(r["strike"] - target))
    return dict(best, expiry=ch["expiry"], right="C" if right.upper().startswith("C") else "P", spot=ch["spot"], time=ch["time"],
                suggested=strike is not None, cost=round(best["ask"] * 100, 2) if best.get("ask") else None)


def board(brief: dict, with_options: bool = False, pause: float = 0.6) -> dict:
    """Today's ideas against live prices. A buy is on track when the stock is beating the average
    tradable stock since the open; a short when it is lagging it."""
    ideas = brief.get("longs", []) + brief.get("shorts", [])
    market = brief.get("universe") or []
    q = quotes([i["ticker"] for i in ideas] + ["SPY"] + market[:0])
    spy = q.get("SPY", {})
    # Only today's predictions can be marked against today's prices: a scan for an earlier session
    # must not be scored on a later day's moves.
    session = pd.Timestamp(spy["time"]).tz_convert("America/New_York").strftime("%Y-%m-%d") if spy.get("time") else None
    current = session is not None and session == brief.get("scan_date")
    rows = []
    def opt(i):
        """The contract the scan suggested, or else the at-the-money call (buy) or put (short) for next week."""
        if not with_options:
            return None
        o, exp = i.get("option"), brief.get("option_expiry") or ""
        try:
            if o:
                parts = o["contract"].split()                  # "NVDA 27 Nov 140 C"
                return option_quote(i["ticker"], exp, float(parts[-2]), parts[-1])
            return option_quote(i["ticker"], exp, None, "C" if i["action"] == "BUY" else "P")
        except Exception:
            return None
    opts = []
    for i in ideas:                                    # one at a time, paced: Yahoo throttles bursts of chain requests
        fresh = ("c", i["ticker"].upper(), brief.get("option_expiry") or "") not in _cache
        opts.append(opt(i))
        if with_options and fresh:
            time.sleep(pause)
    for i, o in zip(ideas, opts):
        x = q.get(i["ticker"])
        sgn = 1 if i["action"] == "BUY" else -1
        rel = (x["chg_open"] - spy["chg_open"]) if current and x and x.get("chg_open") is not None and spy.get("chg_open") is not None else None
        rows.append(dict(ticker=i["ticker"], action=i["action"], quote=x, rel_open=rel,
                         on_track=(rel * sgn > 0) if rel is not None else None, option=o))
    scored = [r for r in rows if r["on_track"] is not None]
    longs = [r["quote"]["chg_open"] for r in rows if current and r["action"] == "BUY" and r["quote"] and r["quote"].get("chg_open") is not None]
    shorts = [r["quote"]["chg_open"] for r in rows if current and r["action"] == "SHORT" and r["quote"] and r["quote"].get("chg_open") is not None]
    return dict(as_of=datetime.now(timezone.utc).isoformat(), scan_date=brief.get("scan_date"), state=spy.get("state"),
                market=spy, rows=rows, on_track=sum(r["on_track"] for r in scored), scored=len(scored),
                book=(float(np.mean(longs)) - float(np.mean(shorts))) / 2 if longs and shorts else None,
                source="Yahoo Finance; quotes can lag by up to 15 minutes")
