"""The plain-language layer: every listed ticker, its filings and news, and the market backdrop.

Reads what the sources collected (all-ticker prices, daily SEC filings, headlines, macro series) plus
the model's latest scan, and writes three things the app shows:

  artifacts/market.json        what helps the bull case, what the risks are, today's important filings
  artifacts/all.json           one row per listed ticker (search, sort, filter)
  artifacts/light/<A-Z>.json   per ticker: summary in plain words, filings, headlines, 6 months of closes

Every sentence here is produced by a fixed rule from a number or a filing, and carries its date.

    python -m stockgpt.tracker
"""
from __future__ import annotations

import json
import re
from collections import defaultdict

import numpy as np
import pandas as pd

from .config import ARTIFACT_DIR as ARTIFACTS, DATA_DIR
from .nlp.sentiment import LexiconScorer

LISTICLE = re.compile(r"^\d+ |stocks? to (buy|watch|own|sell|avoid)|\bprediction:|should you buy|better .* stock|millionaire|prices today", re.I)
TOPICS = [("Federal Reserve and interest rates", r"\bfed\b|federal reserve|powell|fomc|rate cut|rate hike|interest rate|treasur|yield|bond"),
          ("Inflation", r"inflation|\bcpi\b|\bpce\b|consumer prices|producer prices"),
          ("Jobs and the economy", r"jobs|payroll|unemployment|jobless|\bgdp\b|recession|economy|economic|consumer spending|retail sales|housing"),
          ("Trade and tariffs", r"tariff|trade war|trade deal|export|import|restriction|sanction|china"),
          ("Politics and policy", r"congress|senate|white house|president|trump|election|shutdown|tax|regulat|policy|government|lawmakers|supreme court"),
          ("Geopolitics and oil", r"\bwar\b|iran|russia|ukraine|israel|middle east|hormuz|geopolit|attack|opec|\boil\b|crude"),
          ("Markets", r"")]


def _topic(h: str) -> str:
    return next(t for t, pat in TOPICS if re.search(pat, h, re.I))


CYCLICAL, DEFENSIVE = ["XLK", "XLY", "XLI", "XLF"], ["XLU", "XLP", "XLV"]


def _pct(x: float, d: int = 1) -> str:
    return f"{x * 100:+.{d}f}%"


def _day(d) -> str:
    return f"{pd.Timestamp(d):%b} {pd.Timestamp(d).day}"


def _read(name: str) -> pd.DataFrame | None:
    p = DATA_DIR / name
    return pd.read_parquet(p) if p.exists() else None


def _tone(s: float) -> str:
    return "good" if s > 0.15 else "bad" if s < -0.15 else "info"


def macro_summary(wide: pd.DataFrame, liquid: list[str], heads: pd.DataFrame | None) -> dict:
    """Bull case and risks, each a plain sentence backed by one number."""
    bull, risks, neutral = [], [], []

    def add(side, text, value, as_of):
        {"bull": bull, "risk": risks, "neutral": neutral}[side].append(dict(text=text, value=value, as_of=str(pd.Timestamp(as_of).date())))

    as_of = wide.index.max()
    if "SPY" in wide:
        s = wide["SPY"].dropna()
        last, m50, m200 = s.iloc[-1], s.tail(50).mean(), s.tail(200).mean()
        r21 = last / s.iloc[-22] - 1
        if last > m50 and last > m200:
            add("bull", "The S&P 500 is in an uptrend: above both its 50-day and 200-day average price.", f"{_pct(r21)} in a month", s.index[-1])
        elif last < m200:
            add("risk", "The S&P 500 is below its 200-day average price, which is how long downtrends look.", f"{_pct(r21)} in a month", s.index[-1])
        elif r21 < -0.03:
            add("risk", "The S&P 500 has fallen below its 50-day average price and is down over the past month.", f"{_pct(r21)} in a month", s.index[-1])
        else:
            add("neutral", "The S&P 500 has slipped below its 50-day average but holds above the 200-day.", f"{_pct(r21)} in a month", s.index[-1])
        hi = s.tail(252).max()
        if last >= hi * 0.98:
            add("bull", "The S&P 500 is within 2% of its highest level of the past year.", f"{_pct(last / hi - 1)} from the high", s.index[-1])
        elif last <= hi * 0.90:
            add("risk", "The S&P 500 is more than 10% below its high of the past year.", f"{_pct(last / hi - 1)} from the high", s.index[-1])
    liq = [t for t in liquid if t in wide]
    if liq:
        w = wide[liq]
        above = float((w.iloc[-1] > w.tail(50).mean()).mean())
        side = "bull" if above > 0.6 else "risk" if above < 0.4 else "neutral"
        text = {"bull": "Most stocks are rising, not only a few large ones.", "risk": "Most stocks are falling, even if the index holds up.",
                "neutral": "About half of stocks are in an uptrend and half are not."}[side]
        add(side, text, f"{above * 100:.0f}% of liquid stocks above their 50-day average", as_of)
    if all(t in wide for t in CYCLICAL + DEFENSIVE):
        r = wide[CYCLICAL + DEFENSIVE].iloc[-1] / wide[CYCLICAL + DEFENSIVE].iloc[-22] - 1
        gap = r[CYCLICAL].mean() - r[DEFENSIVE].mean()
        if gap > 0.015:
            add("bull", "Investors are favouring growth areas (technology, consumer, industrials, banks) over safe ones.", f"growth ahead by {_pct(gap)} in a month", as_of)
        elif gap < -0.015:
            add("risk", "Investors are moving to safe areas (utilities, staples, health care), which they do when worried.", f"safe areas ahead by {_pct(-gap)} in a month", as_of)

    m = _read("macro_raw.parquet")
    if m is not None:
        def series(c):
            return m[c].dropna() if c in m else pd.Series(dtype=float)
        v = series("mkt_vix")
        if len(v):
            x = v.iloc[-1]
            if x < 16:
                add("bull", "Markets are calm. The VIX, which measures how much investors pay for protection, is low.", f"VIX {x:.1f}", v.index[-1])
            elif x > 22:
                add("risk", "Investors are paying up for protection against a drop. The VIX is high.", f"VIX {x:.1f}", v.index[-1])
            else:
                add("neutral", "Fear is at ordinary levels.", f"VIX {x:.1f}", v.index[-1])
        h = series("fred_hy_spread")
        if len(h) > 21:
            x, ch = h.iloc[-1], h.iloc[-1] - h.iloc[-22]
            if x > 5 or ch > 0.4:
                add("risk", "Lenders are demanding more to lend to weaker companies, an early sign of stress.", f"junk-bond spread {x:.2f} points ({ch:+.2f} in a month)", h.index[-1])
            elif x < 4 and ch <= 0.1:
                add("bull", "Credit is easy: weaker companies can borrow at a small premium, so lenders are not worried.", f"junk-bond spread {x:.2f} points ({ch:+.2f} in a month)", h.index[-1])
            else:
                add("neutral", "Credit markets are steady.", f"junk-bond spread {x:.2f} points ({ch:+.2f} in a month)", h.index[-1])
        t = series("fred_dgs10")
        if len(t) > 21:
            x, ch = t.iloc[-1], t.iloc[-1] - t.iloc[-22]
            if ch <= -0.15:
                add("bull", "Long-term interest rates are falling, which lowers borrowing costs and lifts what future profits are worth.", f"10-year Treasury {x:.2f}% ({ch:+.2f} in a month)", t.index[-1])
            elif ch >= 0.15:
                add("risk", "Long-term interest rates are rising, which raises borrowing costs and weighs on stock prices.", f"10-year Treasury {x:.2f}% ({ch:+.2f} in a month)", t.index[-1])
            else:
                add("neutral", "Long-term interest rates are little changed.", f"10-year Treasury {x:.2f}% ({ch:+.2f} in a month)", t.index[-1])
        c = series("fred_curve_10_2")
        if len(c):
            x = c.iloc[-1]
            if x < 0:
                add("risk", "Short-term rates are above long-term rates. This has come before most recessions.", f"10-year minus 2-year: {x:+.2f} points", c.index[-1])
            else:
                add("neutral", "Long-term rates are above short-term rates, the normal shape.", f"10-year minus 2-year: {x:+.2f} points", c.index[-1])
        o = series("mkt_oil")
        if len(o) > 21:
            ch = o.iloc[-1] / o.iloc[-22] - 1
            if ch > 0.10:
                add("risk", "Oil is rising fast, which feeds inflation and squeezes consumers.", f"oil ${o.iloc[-1]:.0f} ({_pct(ch, 0)} in a month)", o.index[-1])
            elif ch < -0.10:
                add("bull", "Oil is falling, which eases inflation and helps consumers.", f"oil ${o.iloc[-1]:.0f} ({_pct(ch, 0)} in a month)", o.index[-1])
        d = series("mkt_dxy")
        if len(d) > 21:
            ch = d.iloc[-1] / d.iloc[-22] - 1
            if ch > 0.02:
                add("risk", "The dollar is strengthening, which cuts the value of US companies' overseas sales.", f"dollar index {_pct(ch)} in a month", d.index[-1])
            elif ch < -0.02:
                add("bull", "The dollar is weakening, which lifts the value of US companies' overseas sales.", f"dollar index {_pct(ch)} in a month", d.index[-1])
        for col, name in (("fred_epu", "Uncertainty about government policy, measured from newspaper coverage,"),
                          ("politics_gpr", "Geopolitical risk (wars, threats, tensions in the news)")):
            e = series(col)
            if len(e) > 300:
                smooth = e.rolling(7, min_periods=1).mean()
                base = smooth.tail(750)
                z = (smooth.iloc[-1] - base.mean()) / base.std()
                if z > 1:
                    add("risk", f"{name} is well above normal.", f"{z:+.1f} standard deviations vs the past 3 years", e.index[-1])
                elif z < -0.5:
                    add("bull", f"{name} is below normal.", f"{z:+.1f} standard deviations vs the past 3 years", e.index[-1])
                else:
                    add("neutral", f"{name} is around normal.", f"{z:+.1f} standard deviations vs the past 3 years", e.index[-1])

    nb, nr = len(bull), len(risks)
    verdict = ("More supports than risks" if nb >= nr + 2 else "More risks than supports" if nr >= nb + 2 else "Mixed picture")
    news = []
    if heads is not None and len(heads):
        mh = heads[heads["ticker"].str.startswith("_")].sort_values("published_at", ascending=False)
        mh = mh[mh["published_at"] >= mh["published_at"].max() - pd.Timedelta(days=3)].drop_duplicates("headline")
        mh = mh[~mh["headline"].str.contains(LISTICLE.pattern, case=False)]
        sc = LexiconScorer().score(mh["headline"].tolist()) if len(mh) else None
        per = defaultdict(int)
        for i, (_, r) in enumerate(mh.iterrows()):
            topic = _topic(r["headline"])
            per[topic] += 1
            if per[topic] <= 4:
                news.append(dict(topic=topic, headline=r["headline"], source=r["source"], url=r["url"],
                                 time=r["published_at"].isoformat(), tone=_tone(float(sc["sentiment"].iloc[i]))))
        order = [t for t, _ in TOPICS]
        news.sort(key=lambda n: order.index(n["topic"]))
    return dict(verdict=verdict, line=f"{nb} readings support stocks, {nr} are risks, {len(neutral)} are neutral.",
                bull=bull, risks=risks, neutral=neutral, headlines=news, as_of=str(as_of.date()),
                note="These readings describe the backdrop. In our backtest they did not predict the next day's direction, so they are context, not a trading signal.")


def build() -> dict:
    listed = pd.read_parquet(DATA_DIR / "listed.parquet")
    px = pd.read_parquet(DATA_DIR / "prices_all.parquet", columns=["date", "ticker", "close", "volume"])
    px["date"] = pd.to_datetime(px["date"])
    px = px[px["date"] <= px["date"].max()]
    wide = px.pivot(index="date", columns="ticker", values="close").sort_index()
    wide = wide[wide.index >= wide.index.max() - pd.Timedelta(days=400)]
    dvol = (px.assign(dv=px["close"] * px["volume"])[px["date"] >= px["date"].max() - pd.Timedelta(days=30)]
            .groupby("ticker")["dv"].mean())
    as_of = wide.index.max()
    sec = _read("sec_tickers.parquet")
    sector = dict(zip(sec["ticker"], sec["sector"])) if sec is not None else {}
    fil = _read("filings.parquet")
    heads = _read("headlines.parquet")
    earn = _read("earnings.parquet")
    fund = _read("fundamentals.parquet")
    brief = json.loads((ARTIFACTS / "brief.json").read_text()) if (ARTIFACTS / "brief.json").exists() else {}
    acc = json.loads((ARTIFACTS / "accuracy.json").read_text()) if (ARTIFACTS / "accuracy.json").exists() else {"stocks": []}
    accuracy = {r["ticker"]: r for r in acc["stocks"]}
    ideas = {i["ticker"]: i for i in brief.get("longs", []) + brief.get("shorts", [])}
    scores = (json.loads((ARTIFACTS / "scores.json").read_text()) if (ARTIFACTS / "scores.json").exists() else {}).get("stocks", {})
    modelled = set(pd.read_parquet(DATA_DIR / "tickers.parquet")["ticker"]) if (DATA_DIR / "tickers.parquet").exists() else set()

    fmap, hmap = defaultdict(list), defaultdict(list)
    if fil is not None:
        fil = fil[fil["date"] >= as_of - pd.Timedelta(days=45)].sort_values(["date", "importance"], ascending=False)
        for r in fil.itertuples():
            fmap[r.ticker].append(r)
    if heads is not None and len(heads):
        sh = heads[~heads["ticker"].str.startswith("_")].copy()
        sh = sh[sh["published_at"] >= sh["published_at"].max() - pd.Timedelta(days=10)]
        sh = sh[~sh["headline"].str.contains(LISTICLE.pattern, case=False)]
        sc = LexiconScorer().score(sh["headline"].drop_duplicates().tolist())
        sc.index = sh["headline"].drop_duplicates().values
        sh["sent"] = sh["headline"].map(sc["sentiment"])
        for r in sh.sort_values("published_at", ascending=False).itertuples():
            hmap[r.ticker].append(r)
    next_earn, last_fund = {}, {}
    if earn is not None:
        e = earn.assign(report_date=pd.to_datetime(earn["report_date"]))
        nxt = e[e["report_date"] >= as_of].sort_values("report_date").groupby("ticker")["report_date"].first()
        next_earn = nxt.to_dict()
    if fund is not None:
        last_fund = fund.sort_values("report_date").groupby("ticker").last().to_dict("index")

    spy = wide["SPY"].dropna() if "SPY" in wide else None
    spy5 = float(spy.iloc[-1] / spy.iloc[-6] - 1) if spy is not None else 0.0
    cal = wide.index[-126:]                        # shared 6-month calendar; each ticker's closes align to it
    rows, shards, movers = [], defaultdict(lambda: {"_dates": [f"{d:%Y-%m-%d}" for d in cal]}), []
    for t in listed.itertuples():
        s = wide[t.ticker].dropna() if t.ticker in wide else pd.Series(dtype=float)
        has = len(s) >= 2 and s.index[-1] >= as_of - pd.Timedelta(days=7)
        def chg(n):
            return float(s.iloc[-1] / s.iloc[-1 - n] - 1) if has and len(s) > n else None
        d1, w1, m1, m3, y1 = chg(1), chg(5), chg(21), chg(63), chg(min(252, len(s) - 1)) if has and len(s) > 200 else None
        price = float(s.iloc[-1]) if has else None
        dv = float(dvol.get(t.ticker, 0.0)) if has else 0.0
        fl, hl = fmap.get(t.ticker, []), hmap.get(t.ticker, [])
        if hl:                                     # stories that name the company; the rest only tag it
            word = re.sub(r"[^a-z]", "", t.name.lower().split()[0]) if t.name else ""
            pat = re.compile(rf"\b{re.escape(t.ticker)}\b|{re.escape(word)}" if len(word) >= 4 else rf"\b{re.escape(t.ticker)}\b", re.I)
            named = [h for h in hl if pat.search(h.headline)]
            n_named = len(named)
            hl = named if len(named) >= 3 else named + [h for h in hl if h not in named][:3 - len(named)]
        points = []
        if has:
            if w1 is not None and abs(w1) >= 0.03:
                rel = w1 - spy5
                points.append(dict(tone="good" if w1 > 0 else "bad", text=f"Shares are {'up' if w1 > 0 else 'down'} {abs(w1) * 100:.0f}% over the past week"
                              + (f", {'ahead of' if rel > 0 else 'behind'} the market by {abs(rel) * 100:.0f} points." if abs(rel) >= 0.02 else ", in line with the market.")))
            elif w1 is not None:
                points.append(dict(tone="info", text=f"Shares are little changed over the past week ({_pct(w1)})."))
            if m3 is not None and abs(m3) >= 0.10:
                points.append(dict(tone="good" if m3 > 0 else "bad", text=f"Over three months the stock is {'up' if m3 > 0 else 'down'} {abs(m3) * 100:.0f}%."))
            if len(s) > 200:
                hi, lo = s.tail(252).max(), s.tail(252).min()
                if price >= hi * 0.97:
                    points.append(dict(tone="good", text="The price is at or near its highest level of the past year."))
                elif price <= lo * 1.05:
                    points.append(dict(tone="bad", text="The price is at or near its lowest level of the past year."))
        # the three filings that matter most (importance, then recency), shown newest first
        big = sorted(sorted([f for f in fl if f.importance >= 2], key=lambda f: (-f.importance, -f.date.value))[:3], key=lambda f: -f.date.value)
        lead = max(big, key=lambda f: (f.importance + (f.date >= as_of - pd.Timedelta(days=10)), f.date.value)) if big else None
        for f in big:
            points.append(dict(tone=f.tone, text=f"{_day(f.date)}: {f.title} (SEC form {f.form}). {f.why}"))
        ins = [f for f in fl if f.form in ("4", "4/A", "144")]
        if len(ins) >= 3:
            points.append(dict(tone="info", text=f"Insiders reported {len(ins)} share trades or planned sales in the past 45 days."))
        if hl and n_named < 2:
            points.append(dict(tone="info", text=f"Little news naming the company in the past 10 days. Latest story that mentions it: \u201c{hl[0].headline}\u201d ({hl[0].source}, {_day(hl[0].published_at)})."))
        elif hl:
            pos, neg = sum(h.sent > 0.15 for h in hl), sum(h.sent < -0.15 for h in hl)
            mood = "mostly positive" if pos >= 2 * max(neg, 1) else "mostly negative" if neg >= 2 * max(pos, 1) else "mixed"
            points.append(dict(tone="good" if mood == "mostly positive" else "bad" if mood == "mostly negative" else "info",
                               text=f"{len(hl)} news stories in the past 10 days; the wording is {mood}. Latest: “{hl[0].headline}” ({hl[0].source}, {_day(hl[0].published_at)})."))
        ne = next_earn.get(t.ticker)
        if ne is not None and (ne - as_of).days <= 21:
            points.append(dict(tone="watch", text=f"Next earnings report is expected around {_day(ne)}. Stocks often move most on that day."))
        fu = last_fund.get(t.ticker)
        if fu:
            g, om = fu.get("revenue_growth_yoy"), fu.get("operating_margin")
            if g is not None and pd.notna(g) and abs(g) < 5:
                points.append(dict(tone="good" if g > 0.05 else "bad" if g < -0.02 else "info",
                                   text=("Latest reported sales were about flat from a year earlier" if abs(g) < 0.005 else f"Latest reported sales were {'up' if g >= 0 else 'down'} {abs(g) * 100:.0f}% from a year earlier")
                                   + (f", with {'a profit' if om > 0 else 'a loss'} of {abs(om) * 100:.0f} cents per dollar of sales before interest and tax." if om is not None and pd.notna(om) and abs(om) < 3 else ".")))
        idea, a = ideas.get(t.ticker), accuracy.get(t.ticker)
        model = None
        if idea:
            model = dict(action=idea["action"], strength=idea["strength"])
        sc = scores.get(t.ticker)
        if sc:                                     # the model's view today: where it ranks this stock among every stock it scores
            model = {**(model or {}), "pct": sc["pct"], "lean": sc["lean"], "tested": sc["tested"], "of": len(scores)}
        if a:
            model = {**(model or {}), "accuracy": a["rel_accuracy"], "days": a["days"]}
            if idea:
                points.append(dict(tone="info", text=f"The model's idea for {brief.get('scan_date', 'today')} is {idea['action']} ({idea['strength'].lower()} signal). "
                                   f"On this stock it has been right {a['rel_accuracy'] * 100:.0f}% of days over {a['days']:,} tested days."))
        if lead is not None:
            line = f"{lead.title} on {_day(lead.date)}"
        elif hl:
            line = hl[0].headline
        elif has and w1 is not None:
            line = f"No recent filings or news; shares {_pct(w1)} this week"
        else:
            line = "No recent price, filing or news on record"
        if big and has and w1 is not None and abs(w1) >= 0.03:
            line += f"; shares {_pct(w1, 0)} this week"
        light = dict(ticker=t.ticker, name=t.name, exchange=t.exchange, sector=sector.get(t.ticker, ""), price=price,
                     as_of=str(s.index[-1].date()) if has else None, chg=dict(d1=d1, w1=w1, m1=m1, m3=m3, y1=y1),
                     hi52=float(s.tail(252).max()) if has else None, lo52=float(s.tail(252).min()) if has else None,
                     closes=[None if pd.isna(x) else round(float(x), 4 if x < 5 else 2) for x in wide[t.ticker].reindex(cal)] if has else [],
                     summary=dict(line=line, points=points[:8]), modelled=t.ticker in modelled, model=model,
                     filings=[dict(d=str(f.date.date()), title=f.title, why=f.why, tone=f.tone, form=f.form, url=f.url, imp=int(f.importance)) for f in sorted([f for f in fl if f.importance >= 2][:10] + [f for f in fl if f.importance < 2][:6], key=lambda f: -f.date.value)],
                     news=[dict(t=h.published_at.isoformat(), h=h.headline, src=h.source, url=h.url, tone=_tone(h.sent)) for h in hl[:8]])
        key = t.ticker[0] if t.ticker[0].isalpha() else "0"
        shards[key][t.ticker] = light
        tone = lead.tone if lead is not None else ""
        rows.append([t.ticker, t.name, t.exchange, sector.get(t.ticker, ""), price, d1, m1, y1, round(dv), len([f for f in fl if f.importance >= 2]),
                     len(hl), line, tone, (idea or {}).get("action", ""), round(a["rel_accuracy"], 4) if a else None, sc["pct"] if sc else None])
        if has and d1 is not None and dv >= 5e6 and price >= 2:
            movers.append((d1, t.ticker, t.name, price, line))

    (ARTIFACTS / "light").mkdir(parents=True, exist_ok=True)
    for k, v in shards.items():
        (ARTIFACTS / "light" / f"{k}.json").write_text(json.dumps(v, separators=(",", ":")))
    cols = ["ticker", "name", "exchange", "sector", "price", "d1", "m1", "y1", "dollar_volume", "filings", "news", "line", "tone", "action", "accuracy", "score"]
    (ARTIFACTS / "all.json").write_text(json.dumps(dict(cols=cols, rows=rows, as_of=str(as_of.date())), separators=(",", ":"), allow_nan=False, default=lambda o: None).replace("NaN", "null"))

    liquid = [t for t, v in dvol.items() if v >= 20e6]
    macro = macro_summary(wide, liquid, heads)
    latest = None
    if fil is not None and len(fil):
        day = fil["date"].max()
        today = fil[fil["date"] == day]
        top = today[today["importance"] >= 3].assign(dv=lambda d: d["ticker"].map(dvol).fillna(0)).sort_values("dv", ascending=False)
        top = top.drop_duplicates("accession")
        names = dict(zip(listed["ticker"], listed["name"]))
        latest = dict(date=str(day.date()), total=int(today["accession"].nunique()), companies=int(today["cik"].nunique()),
                      by_tone={k: int(v) for k, v in today.drop_duplicates("accession")["tone"].value_counts().items()},
                      important=[dict(ticker=r.ticker, name=names.get(r.ticker, ""), title=r.title, why=r.why, tone=r.tone, form=r.form, url=r.url)
                                 for r in top.head(40).itertuples()])
    movers.sort()
    def mv(x):
        return [dict(ticker=t, name=n, chg=c, price=p, line=l) for c, t, n, p, l in x]
    market = dict(as_of=str(as_of.date()), macro=macro, filings=latest,
                  movers=dict(up=mv(movers[::-1][:8]), down=mv(movers[:8])),
                  counts=dict(listed=len(listed), scored=len(scores), priced=int(sum(r[4] is not None for r in rows)), modelled=len(modelled),
                              with_filings=len(fmap), with_news=len(hmap),
                              by_exchange={k: int(v) for k, v in listed["exchange"].value_counts().items()}),
                  news_through=heads["published_at"].max().isoformat() if heads is not None and len(heads) else None,
                  filings_through=str(fil["date"].max().date()) if fil is not None and len(fil) else None)
    (ARTIFACTS / "market.json").write_text(json.dumps(market, separators=(",", ":"), default=str).replace("NaN", "null"))
    return market


if __name__ == "__main__":
    m = build()
    print(f"tracker: {m['counts']}")
    print(f"macro: {m['macro']['verdict']}: {m['macro']['line']}")
    for side in ("bull", "risks"):
        for x in m["macro"][side]:
            print(f"  [{side}] {x['text']} ({x['value']})")
