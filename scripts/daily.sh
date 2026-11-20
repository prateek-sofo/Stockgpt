#!/usr/bin/env bash
# The daily refresh. Run it every weekday morning on any machine that stays on (a Mac, a server,
# the AWS box) and the app is live: `scripts/local.sh serve` shows the result, `snapshot/` is the
# shareable copy.
#
#   scripts/daily.sh tracker   every listed ticker: prices, yesterday's SEC filings, headlines, market
#                              summary. About 30 minutes. Works on a fresh machine with nothing stored.
#   scripts/daily.sh scan      tracker + today's model ideas. Needs the price history and backtest
#                              from one earlier `scripts/local.sh real` (or `daily.sh full`).
#   scripts/daily.sh full      weekly: refresh SEC bulk files, retrain, rerun the walk-forward backtest.
#   scripts/daily.sh capture   during the session (for example 09:40 and 15:50 ET): note live stock and
#                              option quotes for today's predictions.
#   scripts/daily.sh close     after 16:15 ET: today's closing prices, mark every prediction right or
#                              wrong, update the track record.
#
# A weekday on a machine that stays on:  08:40 scan · 09:40 capture · 15:50 capture · 16:30 close
#
# Data: set SOFO_WORLD_URL and SOFO_WORLD_KEY (a Sofo World service key, read scope) and news,
# financials, filings and earnings dates come from Sofo World, the one place that scrapes.
# SOFO_DATABASE_URL (read-only role) or SOFO_NEWS_URI (s3:// or folder dump) work too. Without
# any of them, the same public sources are read directly (SEC EDGAR, Yahoo headlines); set SEC_CONTACT
# to your email for that (the SEC asks automated clients to name a contact). Prices (Yahoo) and
# rates (FRED) are read here in both cases until Sofo World carries them.
set -euo pipefail
MODE=${1:-tracker}
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RAW=${STOCKGPT_DATA_DIR:-$ROOT/data/raw}; ART=${STOCKGPT_ARTIFACT_DIR:-$ROOT/artifacts}; SEC=$RAW/sec
export SEC_CONTACT=${SEC_CONTACT:-data@sofo.ai}
UA="StockGPT research $SEC_CONTACT"
PY=${PYTHON:-python3}
mkdir -p "$SEC" "$ART"
cd "$ROOT/backend"
step() { echo; echo "== $* ($(date -u +%H:%M) UTC)"; }

if [[ $MODE == capture ]]; then $PY -m stockgpt.journal capture; "$ROOT/scripts/snapshot.sh"; exit 0; fi
if [[ $MODE == close ]]; then
  step "closing prices"
  $PY -m stockgpt.sources.alltickers
  step "mark today's predictions"
  $PY -m stockgpt.journal settle
  $PY -m stockgpt.tracker
  "$ROOT/scripts/snapshot.sh"
  echo; echo "done. Commit published/journal/ to keep the record."
  exit 0
fi

SOFO=${SOFO_DATABASE_URL:-}${SOFO_NEWS_URI:-}
from() { $PY -c "import json;print(json.load(open('$RAW/sources.json')).get('$1',''))" 2>/dev/null || true; }
rm -f "$RAW/sources.json"
sofo_api() { $PY -m stockgpt.sources.sofo_api || echo "Sofo World API not available"; }
if [[ -n ${SOFO_WORLD_URL:-} ]]; then
  # Sofo World is the one place that scrapes. Its API hands over everything new since the last run:
  # news, events, competitor graph, financials, filings. Works from any machine with the URL and a key.
  step "Sofo World API: news, events, competitor graph, financials, filings"
  sofo_api
elif [[ -n $SOFO ]]; then
  # Same data, read from the database (read-only role) or from an S3 / folder dump.
  step "Sofo World: financials, filings, earnings dates"
  $PY -m stockgpt.sources.sofo_finance || echo "Sofo World financials not available"
  step "Sofo World: news, events, competitor graph"
  $PY -m stockgpt.sources.sofo_world --since 2018-01-01 || echo "Sofo World news not available"
else
  echo "SOFO_WORLD_URL (or SOFO_DATABASE_URL / SOFO_NEWS_URI) not set: reading the public sources directly"
fi

# Anything Sofo World did not supply is read directly from the same public source, for now.
if [[ $(from financials) != "Sofo World" ]]; then
  step "listed tickers (SEC, direct)"
  curl -fsS -A "$UA" -o "$SEC/company_tickers_exchange.json" https://www.sec.gov/files/company_tickers_exchange.json
  if [[ $MODE == full || ! -s $SEC/submissions.zip ]]; then
    step "SEC bulk filings history (1.5 GB, direct)"
    sleep 1; curl -fsS -A "$UA" -o "$SEC/submissions.zip" https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip
  fi
  if [[ $MODE == full ]]; then
    step "SEC company financials (1.4 GB, direct)"
    sleep 1; curl -fsS -A "$UA" -o "$SEC/companyfacts.zip" https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip
    $PY -m stockgpt.sources.sec
  fi
fi

# Prices and rates are not in Sofo World's catalog yet, so these two are read here in both cases.
step "prices for every listed ticker"
if [[ $MODE != tracker && -s $RAW/prices.parquet ]]; then $PY -m stockgpt.sources.alltickers --merge
else $PY -m stockgpt.sources.alltickers; fi
step "rates, volatility, policy and geopolitical risk"
$PY -m stockgpt.sources.macro --start 2017-01-01 --no-gdelt || echo "macro refresh failed; keeping the previous series"
if [[ $(from filings) != "Sofo World" ]]; then
  step "SEC filings (direct)"
  $PY -m stockgpt.sources.filings --days 45
fi
if [[ $(from news) != "Sofo World" ]]; then
  step "headlines (Yahoo, direct)"
  $PY -m stockgpt.sources.headlines ${HEADLINE_TOP:+--top $HEADLINE_TOP}
fi

if [[ $MODE != tracker ]]; then
  [[ -s $RAW/prices.parquet && -s $RAW/sec_fundamentals.parquet ]] || { echo "no model history here: run scripts/local.sh real once, or use 'tracker'"; exit 1; }
  if [[ -n ${SOFO_WORLD_URL:-} ]]; then
    step "Sofo World API: news published since the first pull"     # the scan sees articles up to this minute
    sofo_api
  fi
  step "model"
  $PY -m stockgpt.sources.snapshots premarket || true
  $PY -m stockgpt.sources.assemble --start 2019-01-01
  export STOCKGPT_RETRAIN_DAYS=63
  if [[ $MODE == full || ! -s $ART/backtest.json ]]; then $PY -m stockgpt.indexfunds; $PY -m stockgpt.pipeline
  else $PY -m stockgpt.pipeline --scan; fi
  step "record today's predictions (locked once the market opens)"
  $PY -m stockgpt.journal issue || true
  mkdir -p "$ROOT/published"                       # small results a tracker-only machine can reuse
  for f in brief backtest accuracy research ablation universe longterm meta; do cp "$ART/$f.json" "$ROOT/published/" 2>/dev/null || true; done
else
  for f in "$ROOT"/published/*.json; do [[ -e $f && ! -e $ART/$(basename "$f") ]] && cp "$f" "$ART/"; done
fi

step "summaries: market, every ticker"
$PY -m stockgpt.tracker
step "shareable copy"
"$ROOT/scripts/snapshot.sh"
echo; echo "done. Serve it: scripts/local.sh serve   |   static copy: $ROOT/snapshot/index.html"
