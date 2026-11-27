#!/usr/bin/env bash
# The two-minute loop on the AWS box. Reads Sofo World's database directly (read-only role), so a cycle is one
# small query for rows first seen since the last cycle. Outside 04:00 to 20:00 New York time on weekdays it sleeps.
set -euo pipefail
cd /srv/stockgpt/current/backend
source /etc/stockgpt/bucket.env
export STOCKGPT_DATA_DIR=/srv/stockgpt/data/raw STOCKGPT_ARTIFACT_DIR=/srv/stockgpt/artifacts
export SOFO_DATABASE_URL=$(aws ssm get-parameter --region "$AWS_REGION" --with-decryption \
  --name /stockgpt/sofo-database-url --query Parameter.Value --output text)
export STOCKGPT_INTRADAY_SECONDS=${STOCKGPT_INTRADAY_SECONDS:-120}
while true; do
  h=$(TZ=America/New_York date +%-H); d=$(TZ=America/New_York date +%u)
  if (( d <= 5 && h >= 4 && h < 20 )); then
    /srv/stockgpt/venv/bin/python -m stockgpt.intraday once || true
    sleep "$STOCKGPT_INTRADAY_SECONDS"
  else
    sleep 600
  fi
done
