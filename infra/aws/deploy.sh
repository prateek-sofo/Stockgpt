#!/usr/bin/env bash
# StockGPT on AWS, beside the Sofo World company database (db-1, us-east-2). Run from the Mac (aws CLI signed in).
# Prints no secret: passwords go straight from openssl into Postgres and SSM SecureString.
#
#   deploy.sh box                     create or update the instance, bucket and role (stack stockgpt)
#   deploy.sh db DB1_HOST DB1_SG      read-only role sofo_stockgpt on the primary (SOFO_ADMIN_URL), allow the box on
#                                     db-1's security group, store the db-1 URL in SSM /stockgpt/sofo-database-url
#   deploy.sh release                 build the UI, upload the committed HEAD, install it on the box, start the API
#   deploy.sh run [scan|full]         run the data refresh + pipeline now (first time: full, ~1-2 h)
#   deploy.sh tunnel                  open the UI at http://localhost:8000 through SSM (no inbound ports)
#   deploy.sh status
set -euo pipefail

export AWS_PROFILE=${AWS_PROFILE:-sofo-cc} AWS_PAGER=""
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
STACK=stockgpt
R=us-east-2

out() { aws cloudformation describe-stacks --region $R --stack-name $STACK --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }

on_box() {  # run a local shell script on the box through SSM and print its output
  local id cmd status script
  id=$(out BoxId)
  script=$(base64 <"$1" | tr -d '\n')
  cmd=$(aws ssm send-command --region $R --instance-ids "$id" --document-name AWS-RunShellScript --timeout-seconds "${2:-1800}" \
    --parameters "commands=[\"echo $script | base64 -d > /tmp/sg-step.sh && bash /tmp/sg-step.sh; rc=\$?; rm -f /tmp/sg-step.sh; exit \$rc\"]" \
    --query Command.CommandId --output text)
  for _ in $(seq 1 720); do
    status=$(aws ssm get-command-invocation --region $R --command-id "$cmd" --instance-id "$id" --query Status --output text 2>/dev/null || true)
    case $status in Success|Failed|Cancelled|TimedOut) break ;; esac
    sleep 10
  done
  aws ssm get-command-invocation --region $R --command-id "$cmd" --instance-id "$id" --query '[StandardOutputContent,StandardErrorContent]' --output text
  [[ $status == Success ]]
}

box() {
  aws cloudformation deploy --region $R --stack-name $STACK --template-file "$HERE/stockgpt.yaml" --capabilities CAPABILITY_IAM \
    --tags sofo:project=stockgpt --no-fail-on-empty-changeset
  echo "box $(out BoxId), security group $(out GroupId), bucket $(out BucketName)"
}

db() {
  local host=${1:?db-1 private host or IP} sg=${2:?db-1 security group id} pw
  : "${SOFO_ADMIN_URL:?set SOFO_ADMIN_URL to an admin URL of the PRIMARY (the hub via :5434 until cutover)}"
  pw=$(openssl rand -hex 24)
  psql "$SOFO_ADMIN_URL" -v ON_ERROR_STOP=1 -q -v pw="'$pw'" -f "$HERE/grants.sql"
  aws ec2 authorize-security-group-ingress --region $R --group-id "$sg" \
    --ip-permissions "IpProtocol=tcp,FromPort=5432,ToPort=5432,UserIdGroupPairs=[{GroupId=$(out GroupId),Description=stockgpt-read-only}]" \
    2>/dev/null || echo "(ingress rule already present)"
  aws ssm put-parameter --region $R --name /stockgpt/sofo-database-url --type SecureString --overwrite \
    --value "postgresql://sofo_stockgpt:$pw@$host:5432/sofo_world?sslmode=prefer" >/dev/null
  unset pw
  echo "read-only role ready; URL stored in SSM /stockgpt/sofo-database-url"
}

release() {
  local sha tar key bucket step
  sha=$(git -C "$ROOT" rev-parse --short HEAD)
  [[ -z $(git -C "$ROOT" status --porcelain) ]] || echo "warning: uncommitted changes are NOT in the release" >&2
  (cd "$ROOT/frontend" && npm ci --no-audit --no-fund >/dev/null && npm run build >/dev/null)
  tar=$(mktemp -d)/stockgpt-$sha.tar
  git -C "$ROOT" archive --format=tar -o "$tar" HEAD      # the committed tree ...
  tar -C "$ROOT" -rf "$tar" frontend/dist                 # ... plus the built UI
  gzip -f "$tar"; tar=$tar.gz
  bucket=$(out BucketName); key=releases/stockgpt-$sha.tar.gz
  aws s3 cp --only-show-errors "$tar" "s3://$bucket/$key"
  step=$(mktemp)
  cat >"$step" <<EOF
set -euo pipefail
D=/srv/stockgpt/releases/$sha
rm -rf \$D && mkdir -p \$D
aws s3 cp --only-show-errors s3://$bucket/$key /tmp/release.tar.gz
tar -xzf /tmp/release.tar.gz -C \$D && rm -f /tmp/release.tar.gz
[ -x /srv/stockgpt/venv/bin/python ] || python3.12 -m venv /srv/stockgpt/venv
/srv/stockgpt/venv/bin/pip install -q --upgrade pip
/srv/stockgpt/venv/bin/pip install -q -r \$D/backend/requirements.txt
chown -R stockgpt:stockgpt /srv/stockgpt
ln -sfn \$D /srv/stockgpt/current
cp \$D/infra/aws/systemd/* /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now stockgpt-api stockgpt-intraday stockgpt-daily.timer stockgpt-weekly.timer stockgpt-close.timer
systemctl restart stockgpt-api stockgpt-intraday
sleep 3; curl -fsS http://127.0.0.1:8000/api/health
EOF
  on_box "$step" 1800; rm -f "$step"
  echo "released $sha"
}

run() {
  local step; step=$(mktemp)
  echo "sudo -u stockgpt /srv/stockgpt/current/infra/aws/daily.sh ${1:-full} 2>&1 | tail -40" >"$step"
  on_box "$step" 21600; rm -f "$step"
}

tunnel() {
  echo "UI at http://localhost:8000 (Ctrl-C to close)"
  aws ssm start-session --region $R --target "$(out BoxId)" --document-name AWS-StartPortForwardingSession \
    --parameters '{"portNumber":["8000"],"localPortNumber":["8000"]}'
}

status() {
  local step; step=$(mktemp)
  echo "systemctl --no-pager status stockgpt-api | head -5; systemctl list-timers 'stockgpt-*' --no-pager; cat /srv/stockgpt/artifacts/meta.json 2>/dev/null | head -c 600" >"$step"
  on_box "$step" 120; rm -f "$step"
}

cmd=${1:-}; shift || true
case $cmd in
  box) box ;; db) db "$@" ;; release) release ;; run) run "$@" ;; tunnel) tunnel ;; status) status ;;
  *) sed -n 2,13p "$0"; exit 1 ;;
esac
