# StockGPT

News-driven trading desk: every morning before the open it reads overnight news, scores it, runs a per-ticker model on the liquid options universe, and produces ranked trade tickets for **day trades (shares)**, **options**, **earnings straddles**, plus a **long-term** ranking. Every strategy is walk-forward backtested with realistic costs.


## Quick start

```bash
pip install -r backend/requirements.txt
cd backend && python -m stockgpt.pipeline          # ~3 min: features, walk-forward backtest, scan, profiles
uvicorn stockgpt.api:app --port 8000                # API + UI (after building the UI)

cd ../frontend && npm install && npm run build      # UI is served by the API from frontend/dist
# or: npm run dev  (hot reload on :5173, proxies /api to :8000)
```

With no data in `data/raw/`, a synthetic market (300 fictional tickers, 3 years, ~200K headlines, hidden per-ticker behaviour) is generated so the whole stack runs end to end.

Daily job (cron at ~08:55 ET once your scraper has landed overnight news and premarket prices):

```bash
python -m stockgpt.pipeline --scan    # refit + morning brief, ~30s
python -m stockgpt.pipeline           # weekly: full retrain + walk-forward backtest
```

Deploy: `docker build -t stockgpt . && docker run -p 8000:8000 -v $PWD/data:/app/data stockgpt` (Fly.io, Render, Railway, ECS all work with this single container).

## Plug in your data

Drop parquet or CSV files named after each table into `data/raw/`. Full spec in `backend/stockgpt/data/schema.py`.

| table | required columns | notes |
|---|---|---|
| `news` | `published_at` (UTC), `ticker`, `headline` | one row per article × ticker; must be real publication time |
| `prices` | `date, ticker, open, high, low, close, volume` | optional `premarket` (09:00 ET print) |
| `tickers` | `ticker, name, sector` | |
| `earnings` | `ticker, report_date, timing` (BMO/AMC) | optional `eps_actual, eps_estimate`; include future dates |
| `fundamentals` | `ticker, report_date` + ratios | keyed by public date |
| `options` | `date, ticker, iv30, option_volume` | optional `iv_weekly, atm_spread_pct` (ORATS / ThetaData / Polygon) |

LLM news scoring: `STOCKGPT_LLM_SCORING=1` scores headlines with Claude via structured outputs (`nlp/sentiment.py`); defaults to the fast lexicon scorer.

## How it works

1. **Point-in-time features** (`features.py`). Each article is assigned to the first 09:00 ET scan that could see it. Prices through the prior close, plus premarket. 63 features: overnight sentiment (magnitude-weighted), event types, abnormal news volume, gap vs. sector, vol, IV, IV rank, earnings timing and surprise, fundamentals.
2. **A model per ticker, with partial pooling** (`models/ticker_model.py`). Each ticker's own behaviour (news drift, gap follow-through vs. fade, day-to-day reversal, post-earnings drift) is estimated as a ridge-shrunk slope using only its past, pulled toward the market slope until it has enough evidence. The model applies each ticker's own coefficients through interaction terms. A ridge core plus LightGBM predict open-to-close return in vol units, and a second model predicts move size.
3. **Honest probabilities.** Confidence is shrunk by a calibration slope fitted on the model's own prior out-of-sample predictions.
4. **Trade construction** (`options.py`, `backtest.py`). Option P&L is integrated over the predicted move distribution for ATM and in-the-money strikes. Option EV is then corrected by its own realised track record (removes the winner's-curse bias of picking the highest-EV names).
5. **Risk.** Liquidity gate (price, $ volume, options volume, spread), quarter Kelly, ≤2% premium per trade, ≤8% per day, size halved after a −5% day.
6. **Walk-forward backtest.** Monthly refit, expanding window, 1-day embargo. Options priced with Black-Scholes on front-week IV, open IV marked up by the premarket gap, one session of theta, post-earnings IV crush, half-spread paid each side, commissions.

## Demo results (synthetic market, out-of-sample, Jan 2025 → Nov 2026)

| strategy | total | Sharpe | max DD | notes |
|---|---|---|---|---|
| Shares day trade | +200% | 1.9 | −15% | 53.6% win rate, profit factor 1.25 |
| Earnings straddles | +12% | 0.8 | −6% | ~1% risk per trade, selective |
| Options day trade | −3% | −0.1 | −16% | EV calibration cut trading to 90 trades |
| Long-term top quintile | +10.4%/yr vs −4.9% universe | IR 1.9 | | beat universe in 72% of months |

Signal: daily IC 0.050; top-minus-bottom decile ≈ 40 bps/day. Intraday long options did not overcome theta + spread + IV premium in this market; the system learned to mostly stay out. These numbers describe the synthetic market only. Rerun on real data to measure the real edge.

## Layout

```
backend/stockgpt/
  data/        schema (data contract), loader, synthetic market
  nlp/         lexicon + Claude news scorers
  features.py  point-in-time panel + per-ticker traits
  models/      hierarchical per-ticker model
  options.py   pricing, EV integration, Kelly
  backtest.py  walk-forward, simulation, metrics, Monte Carlo
  scanner.py   morning brief
  longterm.py  monthly fundamentals/news ranking
  pipeline.py  orchestration -> artifacts/*.json
  api.py       FastAPI + static UI
frontend/      React + Vite + Tailwind + Recharts
```

Research software. Not investment advice. Options can lose 100% of premium.
