# StockGPT

News-driven trading desk: every morning before the open it reads overnight news, scores it, runs a per-ticker model on the liquid options universe, and produces ranked trade tickets for **day trades (shares)**, **options**, **earnings straddles**, plus a **long-term** ranking. Every strategy is walk-forward backtested with realistic costs.


## Run it locally (one command)

```bash
git clone https://github.com/prateek-sofo/Stockgpt && cd Stockgpt
scripts/local.sh demo     # synthetic market, ~5 min  -> http://localhost:8000
scripts/local.sh real     # real public data (SEC, Yahoo Finance, FRED): 1.5-3 h first time, ~16 GB RAM
scripts/local.sh serve    # restart the app on what was built last
```

Pages: Morning Brief · **Model Accuracy** (per-stock and index-fund backtests) · Backtest Lab (strategies,
research holdout, ablation, deflated Sharpe) · Growth Planner · Universe · Long-term · Data & Models.
Research basis: [docs/RESEARCH.md](docs/RESEARCH.md).

## Keep it live: the daily refresh

```bash
scripts/daily.sh tracker   # every listed ticker (10,400): prices, SEC filings, headlines, market summary. ~30 min
scripts/daily.sh scan      # tracker + today's model ideas (needs one earlier `scripts/local.sh real`)
scripts/daily.sh full      # weekly: refresh SEC bulk files, retrain, rerun the backtest
```

Run it from cron or launchd every weekday morning on a machine that stays on, and the app updates
itself. What it adds on top of the model:

* **Every listed ticker** (`sources/alltickers.py`): the SEC's full ticker list with one year of prices.
* **Every SEC filing, daily** (`sources/filings.py`): EDGAR's daily index, with each filing labelled in
  plain words (what it is, why it matters, good / bad / worth a look), including what each 8-K is about.
* **Headlines** (`sources/headlines.py`): per ticker and for the economy, policy and politics. Headline,
  publisher, time and link only.
* **Plain-language summaries** (`tracker.py`): a one-line read and a short list for every stock, and a
  Market page listing what helps the bull case and what the risks are, each with the number behind it.
  Every sentence comes from a fixed rule on a dated number or filing; nothing is free-written.

What Sofo World news must provide to be used by the model: `docs/SOFO_WORLD_NEWS_REQUIREMENTS.md`.

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

## Real data: SEC + Sofo World + prices

```bash
cd backend
# 1. SEC (free): tickers + sectors, point-in-time XBRL fundamentals, earnings dates with BMO/AMC timing
curl -A "StockGPT research you@example.com" -o ../data/raw/sec/companyfacts.zip https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip
curl -A "StockGPT research you@example.com" -o ../data/raw/sec/submissions.zip  https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip
curl -A "StockGPT research you@example.com" -o ../data/raw/sec/company_tickers_exchange.json https://www.sec.gov/files/company_tickers_exchange.json
python -m stockgpt.sources.sec                      # ~4 min: 7.6k tickers, 150k filings, 134k earnings dates

# 2. Sofo World news + events (read-only role; from a machine that reaches the hub, e.g. the Mac tunnel :5434)
SOFO_DATABASE_URL=postgresql://<readonly>@127.0.0.1:5434/sofo_world python -m stockgpt.sources.sofo_world --since 2018-01-01

# 3. Daily prices (needs a key): Polygon grouped-daily = 1 request per day for all US stocks
POLYGON_API_KEY=... python -m stockgpt.sources.prices --provider polygon --start 2016-01-01
#    or TIINGO_API_KEY=... --provider tiingo, or --provider csv for files in data/raw/prices/

# 4. Merge into the contract and run
python -m stockgpt.sources.assemble --start 2018-01-01
python -m stockgpt.pipeline
```

Point-in-time guarantees: fundamentals are keyed by SEC *filing* date; earnings timing comes from the 8-K Item 2.02 acceptance timestamp (intraday releases count as after-close); Sofo World articles count from the later of publication and first crawl (capped at +6h), and dated events count from the next morning. Without an options feed, IV is estimated from realised volatility (flagged as `iv_source: estimated`), so option results are indicative until ORATS/ThetaData/Polygon options history is connected. EPS surprise uses year-ago EPS as the estimate until consensus estimates are connected.

## Running on AWS beside Sofo World (db-1)

Sofo World's company database (news, events, finance crawl) moves to **db-1** in us-east-2, a private instance. StockGPT
runs on its own instance in db-1's VPC and reads it with a read-only role, the same pattern as the Command Center's cc-1.
No inbound ports; the UI opens through an SSM tunnel. From the Mac (aws CLI signed in, hub tunnel on :5434):

```bash
infra/aws/deploy.sh box                                   # r7g.large, bucket, IAM role (stack "stockgpt", us-east-2)
SOFO_ADMIN_URL=postgresql://<admin>@127.0.0.1:5434/sofo_world \
  infra/aws/deploy.sh db <db-1 private IP> <db-1 security group id>   # read-only role, SG rule, SSM URL
infra/aws/deploy.sh release                               # build UI, ship HEAD, start API + timers
infra/aws/deploy.sh run full                              # first full data load + backtest (~1-2 h)
infra/aws/deploy.sh tunnel                                # http://localhost:8000
```

Schedule on the box: weekdays 08:40 New York time (prices update, Sofo World news, morning scan; every brief is kept in
S3 under `briefs/` as the live track record) and Sundays 06:00 (SEC bulk refresh, full retrain and backtest).

## How it works

1. **Point-in-time features** (`features.py`). Each article is assigned to the first 09:00 ET scan that could see it. Prices through the prior close, plus premarket. 63 features: overnight sentiment (magnitude-weighted), event types, abnormal news volume, gap vs. sector, vol, IV, IV rank, earnings timing and surprise, fundamentals.
2. **A model per ticker, with partial pooling** (`models/ticker_model.py`). Each ticker's own behaviour (news drift, gap follow-through vs. fade, day-to-day reversal, post-earnings drift) is estimated as a ridge-shrunk slope using only its past, pulled toward the market slope until it has enough evidence. The model applies each ticker's own coefficients through interaction terms. A ridge core plus LightGBM predict open-to-close return in vol units, and a second model predicts move size.
3. **Honest probabilities.** Confidence is shrunk by a calibration slope fitted on the model's own prior out-of-sample predictions.
4. **Trade construction** (`options.py`, `backtest.py`). Option P&L is integrated over the predicted move distribution for ATM and in-the-money strikes. Option EV is then corrected by its own realised track record (removes the winner's-curse bias of picking the highest-EV names).
5. **Risk.** Liquidity gate (price, $ volume, options volume, spread), quarter Kelly, ≤2% premium per trade, ≤8% per day, size halved after a −5% day.
6. **Walk-forward backtest.** Monthly refit, expanding window, 1-day embargo. Options priced with Black-Scholes on front-week IV, open IV marked up by the premarket gap, one session of theta, post-earnings IV crush, half-spread paid each side, commissions.

## Competitors, the political world, and daily snapshots

* **Competitors and peers** (`peers.py`): Sofo World's competitor / customer / supplier graph (exported with the
  news), SEC industry peers (4-digit SIC, widened when small) and return-correlation peers (top 10, re-estimated
  quarterly from past data). Features: peers' overnight news, peers' earnings surprises since the last close,
  peers' earnings-day reactions, customer/supplier news.
* **Political world and macro** (`sources/macro.py`): Economic Policy Uncertainty (daily, newspaper-based, FRED),
  Caldara-Iacoviello Geopolitical Risk (daily, acts and threats), rates and curve, credit spreads, breakevens,
  VIX, dollar, oil, gold, S&P 500, sector ETFs; GDELT theme timelines when GDELT allows. Shown on the brief.
* **Snapshots** (`sources/snapshots.py`): premarket prints before the scan (60-day backfill), and after the close
  the option chains (front-week and 30-day ATM IV, volume, spread) and consensus EPS. History free data does not
  have accumulates from the first day; the backtest uses real IV and consensus wherever recorded.

## Real-data results (walk-forward, Jan 2020 → Nov 2026, 2,591 US companies, costs included)

Holdout ablation (`python -m stockgpt.ablation`, train < 2024, test 2024-01 → 2026-11, liquid names):

| target | features | IC | t | top-bottom decile, bps/day |
|---|---|---|---|---|
| raw open->close | price | 0.017 | 3.1 | 15 |
| raw | + earnings / fundamentals | 0.020 | 4.2 | 14 |
| raw | + competitors / peers | 0.019 | 3.9 | 12 |
| raw | + macro / political world | 0.016 | 2.3 | 20 |
| **vs market** | price + earnings / fundamentals | **0.021** | 3.1 | **25** |
| vs market | + macro / political world | 0.016 | 2.4 | 16 |

Market-direction model from the political world: 50.6% of sessions right vs 48.7% up-days (Sharpe 0.28): no edge.

**Model v3 (research-driven: 3-seed tree ensemble, stock-relative target, literature signals), full walk-forward
2020-01 -> 2026-11:** IC 0.018 (was 0.009); per-stock accuracy vs the market: median 51.0%, 51.7% on strong-signal
days; 426 of 2,588 stocks are significantly better than a coin flip where ~59 would be by luck. After costs the
25x25 daily long/short book returned +52% in total with Sharpe 0.35 and a -68% drawdown (2022: -47%, 2025: +81%);
its deflated Sharpe probability is 21%, well short of the 95% bar. The 5-day swing book and option day trades lost
money. Index funds: 50-53% next-session accuracy, at or below "always up" for most funds.

What this means: on prices, filings, earnings and the political world alone, the signal is real but small
(IC ~0.01-0.02). A daily market-neutral long/short book of the top/bottom names earns ~10-18 bps gross per day
and roughly 0-3 bps after 6 bps round-trip costs, with losing years; the backtested share and option strategies lose
money after costs. The missing input is company news (Sofo World); every feature and adapter for it is in place.

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
