#!/usr/bin/env bash
# StockGPT data refresh + run, on the AWS box (systemd: stockgpt-daily.timer, stockgpt-weekly.timer).
#   daily.sh scan     weekdays 08:40 ET: new prices, Sofo World news since yesterday, SEC weekly-ish, morning scan
#   daily.sh full     Sundays: refresh SEC bulk files, full retrain and walk-forward backtest
#   daily.sh close    weekdays 16:30 ET: record option chains and consensus estimates (history we own)
set -euo pipefail
MODE=${1:-scan}
cd /srv/stockgpt/current/backend
source /etc/stockgpt/bucket.env
export STOCKGPT_DATA_DIR=/srv/stockgpt/data/raw STOCKGPT_ARTIFACT_DIR=/srv/stockgpt/artifacts
export SOFO_DATABASE_URL=$(aws ssm get-parameter --region "$AWS_REGION" --with-decryption \
  --name /stockgpt/sofo-database-url --query Parameter.Value --output text)
PY=/srv/stockgpt/venv/bin/python
UA="StockGPT research data@sofo.ai"
SEC=$STOCKGPT_DATA_DIR/sec
mkdir -p "$SEC"

fetch_sec() {   # SEC bulk files: one request each (fair-access friendly)
  curl -fsS -A "$UA" -o "$SEC/company_tickers_exchange.json" https://www.sec.gov/files/company_tickers_exchange.json
  sleep 1; curl -fsS -A "$UA" -o "$SEC/companyfacts.zip" https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip
  sleep 1; curl -fsS -A "$UA" -o "$SEC/submissions.zip"  https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip
  $PY -m stockgpt.sources.sec
}

if [[ $MODE == close ]]; then $PY -m stockgpt.sources.snapshots close; exit 0; fi
if [[ ! -d $STOCKGPT_DATA_DIR/snapshots/premarket ]]; then $PY -m stockgpt.sources.snapshots backfill || true; fi
if [[ $MODE == full || ! -f $STOCKGPT_DATA_DIR/sec_tickers.parquet ]]; then fetch_sec; fi
if [[ -f $STOCKGPT_DATA_DIR/prices.parquet ]]; then
  $PY -m stockgpt.sources.prices --provider yahoo --update
else
  $PY -m stockgpt.sources.prices --provider yahoo --start 2016-01-01
fi
$PY -m stockgpt.sources.sofo_world --since 2018-01-01        # news, events, competitor graph
$PY -m stockgpt.sources.macro --start 2017-01-01 --no-gdelt   # policy uncertainty, geopolitics, rates, markets
if [[ $MODE == scan ]]; then $PY -m stockgpt.sources.snapshots premarket; fi
$PY -m stockgpt.sources.assemble --start 2019-01-01
export STOCKGPT_RETRAIN_DAYS=63
if [[ $MODE == full ]]; then $PY -m stockgpt.pipeline; else $PY -m stockgpt.pipeline --scan; fi

# keep a dated copy of every brief (what the model said before the open, for the track record)
aws s3 cp --only-show-errors "$STOCKGPT_ARTIFACT_DIR/brief.json" "s3://$STOCKGPT_BUCKET/briefs/$(TZ=America/New_York date +%F).json"
curl -fsS -X POST http://127.0.0.1:8000/api/health >/dev/null 2>&1 || true
