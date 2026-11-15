"""End-to-end run: load -> features -> walk-forward backtest -> final model -> morning scan ->
per-ticker profiles -> JSON artifacts consumed by the API/UI.

    python -m stockgpt.pipeline            # full run
    python -m stockgpt.pipeline --scan     # only refit + morning scan (daily job, ~1 min)
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import longterm
from .backtest import (build_signals, calibration, deciles, information_coefficient, monte_carlo,
                       monthly_returns, simulate, stats, walk_forward)
from .config import ARTIFACT_DIR, SETTINGS
from .data.loader import load_all
from .features import ET, TRAITS, _assign_sessions, build_panel, feature_columns, score_news
from .models.ticker_model import HierarchicalModel
from .options import bs_price
from .scanner import run_scan
from .universe import liquid_mask


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else round(float(o), 6)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp,)):
        return str(o.date())
    return o


def _write(name: str, obj) -> None:
    path = ARTIFACT_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clean(obj), separators=(",", ":")))


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def final_model(panel, features, cal_slope):
    last = panel.index.get_level_values("date").max()
    train = panel[panel.index.get_level_values("date") < last]
    m = HierarchicalModel(features).fit(train)
    m.cal_slope = cal_slope
    return m


def _num(row, col, fn=float):
    v = row.get(col, np.nan)
    return fn(v) if pd.notna(v) else None


def _row_stats(row) -> dict:
    lov = row.get("log_opt_vol20", np.nan)
    return dict(rv20=_num(row, "rv20"), iv30=_num(row, "iv30"), iv_rank=_num(row, "iv_rank"),
                adv=float(10 ** row["log_adv20"]) if pd.notna(row.get("log_adv20")) else None,
                opt_vol=float(10 ** lov - 1) if pd.notna(lov) else None,
                spread=_num(row, "atm_spread"), days_to_earn=_num(row, "days_to_earn", int),
                hist_earn_move=_num(row, "hist_earn_move"))


def ticker_profiles(panel, sig, trades, tables, model):
    meta = tables["tickers"].set_index("ticker")
    news = score_news(tables["news"])
    dates = panel.index.get_level_values("date").unique().sort_values()
    news = _assign_sessions(news, dates)
    last = dates[-1]
    latest = panel.xs(last, level="date")
    lm = liquid_mask(panel).xs(last, level="date")
    index = []
    for tk in panel.index.get_level_values("ticker").unique():
        p = panel.xs(tk, level="ticker")
        s = sig.xs(tk, level="ticker") if tk in sig.index.get_level_values("ticker") else None
        hist = p.tail(260)
        prices = [dict(d=str(d.date()), o=r.open_px, h=r.high_px, l=r.low_px, c=r.close_px,
                       s=r.news_on_wsent, n=int(r.news_on_count), e=int(r.is_earnings))
                  for d, r in hist.iterrows() if pd.notna(r.close_px)]
        nn = news[news["ticker"] == tk].sort_values("published_at", ascending=False).head(60)
        nitems = [dict(t=r.published_at.tz_convert(ET).strftime("%Y-%m-%d %H:%M"), h=r.headline,
                       s=float(r.sentiment), m=float(r.magnitude), e=r.event, src=getattr(r, "source", ""),
                       d=str(r.date.date()), on=bool(r.overnight))
                  for r in nn.itertuples()]
        traits = p[[f"tr_{f}" for f in TRAITS if f"tr_{f}" in p]].iloc[::5]
        trait_hist = [dict(d=str(d.date()), **{k[3:]: float(v) for k, v in r.items()}) for d, r in traits.iterrows()]
        # earnings history: implied (pre-event straddle) vs realised close-to-close move
        er = p[p["is_earnings"] == 1]
        earn = []
        for d, r in er.iterrows():
            if pd.isna(r.prev_close) or pd.isna(r.close_px):
                continue
            iv = r.ivw_prev if pd.notna(r.ivw_prev) else r.iv30
            mid = bs_price(r.prev_close, r.prev_close, 7 / 365, iv, 0.045, True) + \
                bs_price(r.prev_close, r.prev_close, 7 / 365, iv, 0.045, False)
            earn.append(dict(d=str(d.date()), implied=float(mid / r.prev_close), actual=float(abs(r.r_cc)),
                             signed=float(r.r_cc), surprise=float(r.eps_surprise) if pd.notna(r.eps_surprise) else None))
        perf = {}
        if s is not None and len(s):
            ok = s["z_oc"].notna()
            ss = s[ok]
            conf = ss[ss["conf"] >= SETTINGS.risk.min_edge_prob]
            perf = dict(days=int(len(ss)), ic=float(ss["mu"].corr(ss["z_oc"], method="spearman")),
                        hit_all=float(ss["hit"].mean()), hit_conf=float(conf["hit"].mean()) if len(conf) else None,
                        n_conf=int(len(conf)),
                        signal_ret_bps=float((np.sign(ss["mu"]) * ss["r_oc"]).mean() * 1e4))
            tt = trades[trades["ticker"] == tk] if len(trades) else trades
            perf["trades"] = {st: dict(n=int(len(g)), win=float((g["ret"] > 0).mean()), pnl=float(g["pnl"].sum()))
                              for st, g in tt.groupby("strategy")} if len(tt) else {}
            perf["recent_trades"] = tt.tail(25).to_dict("records") if len(tt) else []
        row = latest.loc[tk] if tk in latest.index else None
        prof = dict(
            ticker=tk, name=meta.at[tk, "name"], sector=meta.at[tk, "sector"],
            market_cap=float(meta.at[tk, "market_cap"]) if "market_cap" in meta else None,
            liquid=bool(lm.get(tk, False)), prices=prices, news=nitems, traits=trait_hist,
            earnings=earn, performance=perf,
            model=model.ticker_model(row) if row is not None else {},
            stats=_row_stats(row) if row is not None else {},
        )
        _write(f"tickers/{tk}.json", prof)
        index.append(dict(ticker=tk, name=prof["name"], sector=prof["sector"], liquid=prof["liquid"],
                          price=prices[-1]["c"] if prices else None,
                          chg=float(p["r_cc"].iloc[-1]) if len(p) else None,
                          rv20=prof["stats"].get("rv20"), iv30=prof["stats"].get("iv30"),
                          adv=prof["stats"].get("adv"), opt_vol=prof["stats"].get("opt_vol"),
                          spread=prof["stats"].get("spread"), days_to_earn=prof["stats"].get("days_to_earn"),
                          news_drift=prof["model"].get("news_on_wsent", {}).get("effective"),
                          gap_follow=prof["model"].get("gap_pre_z", {}).get("effective"),
                          ic=perf.get("ic"), hit=perf.get("hit_conf"),
                          spark=[x["c"] for x in prices[-60:]]))
    _write("universe.json", index)


def run(scan_only: bool = False, reuse: bool = False):
    t0 = time.time()
    log("loading data")
    tables, source = load_all()
    log(f"source={source}  tickers={len(tables['tickers'])}  news={len(tables['news'])}")
    panel = build_panel(tables)
    feats = feature_columns(panel)
    log(f"panel {panel.shape}  features={len(feats)}")

    bt_path = ARTIFACT_DIR / "oos_predictions.parquet"
    if (scan_only or reuse) and bt_path.exists():
        preds = pd.read_parquet(bt_path)
        bt_json = ARTIFACT_DIR / "backtest.json"
        cal = json.loads(bt_json.read_text())["model"]["cal_slope"] if bt_json.exists() else 0.5
        if reuse and preds.index.nlevels == 2:
            preds = preds[preds.index.isin(panel.index)]
    else:
        log("walk-forward backtest")
        preds, info = walk_forward(panel, feats, progress=lambda a, b: log(f"  block {a}/{b}") if a % 5 == 0 else None)
        preds.to_parquet(bt_path)
        cal = info["last_model"].cal_slope

    sig = build_signals(panel, preds)
    res = simulate(sig)
    curve, trades = res["curve"], res["trades"]
    log("final model + scan")
    model = final_model(panel, feats, cal)

    if not scan_only:
        strat = {}
        for col, key in (("options", "options_daytrade"), ("stock", "stock_daytrade"), ("straddle", "earnings_straddle")):
            strat[key] = stats(curve[col], trades[trades["strategy"] == key] if len(trades) else None)
            strat[key]["monthly"] = monthly_returns(curve, col)
        mc = {col: monte_carlo(curve, col, start=SETTINGS.risk.starting_capital) for col in ("options", "stock")}
        lt = longterm.backtest(panel)
        top_tickers = (sig[sig["liquid"] & sig["z_oc"].notna()].groupby(level="ticker")
                       .apply(lambda g: g["mu"].corr(g["z_oc"], method="spearman") if len(g) >= 60 else np.nan)
                       .dropna().sort_values())
        _write("backtest.json", dict(
            period=dict(start=curve["date"].iloc[0], end=curve["date"].iloc[-1], days=len(curve)),
            strategies=strat, curve=curve.to_dict("records"),
            signal=dict(ic=information_coefficient(sig), calibration=calibration(sig), deciles=deciles(sig)),
            montecarlo=mc, longterm=lt,
            model=dict(cal_slope=cal, importances=model.importances(), coefficients=model.coefficients(),
                       features=len(feats)),
            ticker_ic=dict(best=[dict(ticker=k, ic=float(v)) for k, v in top_tickers.tail(10)[::-1].items()],
                           worst=[dict(ticker=k, ic=float(v)) for k, v in top_tickers.head(10).items()],
                           dist=np.histogram(top_tickers.dropna(), bins=20)[0].tolist(),
                           edges=np.histogram(top_tickers.dropna(), bins=20)[1].tolist()),
            trades=trades.tail(800).to_dict("records") if len(trades) else [],
            costs=SETTINGS.costs.__dict__, risk=SETTINGS.risk.__dict__,
        ))
        _write("longterm.json", dict(ranking=longterm.current_ranking(panel, tables["tickers"]), backtest=lt))
        log("ticker profiles")
        ticker_profiles(panel, sig, trades, tables, model)

    brief = run_scan(panel, tables, model)
    _write("brief.json", brief)
    dates = panel.index.get_level_values("date").unique()
    _write("meta.json", dict(source=source, generated_at=datetime.now(timezone.utc).isoformat(),
                             data_start=str(dates.min().date()), data_end=str(dates.max().date()),
                             tickers=int(len(tables["tickers"])), news=int(len(tables["news"])),
                             universe=brief["universe_size"], features=len(feats),
                             iv_source=panel.attrs.get("iv_source", "vendor"),
                             runtime_sec=round(time.time() - t0, 1),
                             config=dict(universe=SETTINGS.universe.__dict__, risk=SETTINGS.risk.__dict__,
                                         options=SETTINGS.options.__dict__)))
    log(f"done in {time.time() - t0:.0f}s -> {ARTIFACT_DIR}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true", help="refit final model and run the morning scan only")
    ap.add_argument("--reuse-backtest", action="store_true",
                    help="reuse artifacts/oos_predictions.parquet instead of re-running the walk-forward")
    args = ap.parse_args()
    run(scan_only=args.scan, reuse=args.reuse_backtest)
