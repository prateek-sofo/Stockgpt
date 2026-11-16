#!/usr/bin/env bash
# Run StockGPT on your own machine.
#
#   scripts/local.sh demo    synthetic market, ~5 min: installs, builds the UI, runs the pipeline, serves
#   scripts/local.sh real    real public data (SEC + Yahoo Finance + FRED), 1.5-3 h the first time:
#                            ~3 GB of SEC bulk files, ~40 min of price downloads, ~1 h walk-forward backtest.
#                            Needs ~16 GB RAM. Re-running reuses everything already downloaded.
#   scripts/local.sh serve   just start the app with whatever was built last
#
# Then open http://localhost:8000
set -euo pipefail
MODE=${1:-demo}
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
PORT=${PORT:-8000}
UA="StockGPT research ${SEC_CONTACT:-you@example.com}"     # SEC asks for a contact address: export SEC_CONTACT=...

command -v python3 >/dev/null || { echo "python3 (3.10+) is required"; exit 1; }
command -v npm >/dev/null || { echo "node + npm (18+) are required"; exit 1; }

if [[ ! -d .venv ]]; then python3 -m venv .venv; fi
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r backend/requirements.txt xlrd

if [[ ! -d frontend/dist || frontend/src -nt frontend/dist ]]; then
  (cd frontend && npm install --no-audit --no-fund >/dev/null && npm run build >/dev/null)
  echo "UI built"
fi

cd backend
case $MODE in
  demo)
    STOCKGPT_DATA_DIR="$ROOT/data/demo-empty" python -m stockgpt.pipeline ;;
  real)
    RAW="$ROOT/data/raw"; SEC="$RAW/sec"; mkdir -p "$SEC"
    fetch() { [[ -s "$2" ]] || { echo "downloading $(basename "$2")"; curl -fSL -A "$UA" -o "$2" "$1"; sleep 1; }; }
    fetch https://www.sec.gov/files/company_tickers_exchange.json "$SEC/company_tickers_exchange.json"
    fetch https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip "$SEC/companyfacts.zip"
    fetch https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip "$SEC/submissions.zip"
    [[ -s "$RAW/sec_fundamentals.parquet" ]] || python -m stockgpt.sources.sec
    if [[ -s "$RAW/prices.parquet" ]]; then python -m stockgpt.sources.prices --provider yahoo --update
    else python -m stockgpt.sources.prices --provider yahoo --start 2016-01-01; fi
    python -m stockgpt.sources.macro --start 2017-01-01 --no-gdelt
    [[ -d "$RAW/snapshots/premarket" ]] || python -m stockgpt.sources.snapshots backfill || true
    # Sofo World news, if you can reach it:  SOFO_DATABASE_URL=postgresql://... or SOFO_NEWS_URI=s3://...
    if [[ -n "${SOFO_DATABASE_URL:-}${SOFO_NEWS_URI:-}" ]]; then python -m stockgpt.sources.sofo_world --since 2018-01-01; fi
    python -m stockgpt.sources.assemble --start 2019-01-01
    python -m stockgpt.indexfunds
    STOCKGPT_RETRAIN_DAYS=63 python -m stockgpt.pipeline ;;
  serve) ;;
  *) sed -n 2,11p "$ROOT/scripts/local.sh"; exit 1 ;;
esac
[[ $MODE == demo ]] && export STOCKGPT_DATA_DIR="$ROOT/data/demo-empty"
echo; echo "StockGPT is up: http://localhost:$PORT   (Ctrl-C to stop)"
exec uvicorn stockgpt.api:app --port "$PORT"
