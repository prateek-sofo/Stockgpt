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
#
# All sources are public and keyless: SEC EDGAR, Yahoo Finance, FRED. Set SEC_CONTACT to your email
# (the SEC asks automated clients to name a contact).
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

step "listed tickers"
curl -fsS -A "$UA" -o "$SEC/company_tickers_exchange.json" https://www.sec.gov/files/company_tickers_exchange.json
if [[ $MODE == full || ! -s $SEC/submissions.zip ]]; then
  step "SEC bulk filings history (1.5 GB)"
  sleep 1; curl -fsS -A "$UA" -o "$SEC/submissions.zip" https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip
fi
if [[ $MODE == full ]]; then
  step "SEC company financials (1.4 GB)"
  sleep 1; curl -fsS -A "$UA" -o "$SEC/companyfacts.zip" https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip
  $PY -m stockgpt.sources.sec
fi

step "prices for every listed ticker"
if [[ $MODE != tracker && -s $RAW/prices.parquet ]]; then $PY -m stockgpt.sources.alltickers --merge
else $PY -m stockgpt.sources.alltickers; fi
step "SEC filings"
$PY -m stockgpt.sources.filings --days 45
step "rates, volatility, policy and geopolitical risk"
$PY -m stockgpt.sources.macro --start 2017-01-01 --no-gdelt || echo "macro refresh failed; keeping the previous series"
step "headlines"
$PY -m stockgpt.sources.headlines ${HEADLINE_TOP:+--top $HEADLINE_TOP}

if [[ $MODE != tracker ]]; then
  [[ -s $RAW/prices.parquet && -s $RAW/sec_fundamentals.parquet ]] || { echo "no model history here: run scripts/local.sh real once, or use 'tracker'"; exit 1; }
  step "model"
  $PY -m stockgpt.sources.snapshots premarket || true
  $PY -m stockgpt.sources.assemble --start 2019-01-01
  export STOCKGPT_RETRAIN_DAYS=63
  if [[ $MODE == full || ! -s $ART/backtest.json ]]; then $PY -m stockgpt.indexfunds; $PY -m stockgpt.pipeline
  else $PY -m stockgpt.pipeline --scan; fi
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
