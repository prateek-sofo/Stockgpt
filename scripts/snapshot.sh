#!/usr/bin/env bash
# Build the static, shareable copy of the app (no server needed) into snapshot/.
# It bundles the latest results: today's ideas, the market page, every listed ticker, and the full
# model page for today's ideas and the most traded stocks.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ART=${STOCKGPT_ARTIFACT_DIR:-$ROOT/artifacts}
OUT=$ROOT/snapshot
cd "$ROOT/frontend"
[[ -d node_modules ]] || npm install --no-audit --no-fund >/dev/null
rm -rf "$OUT"; mkdir -p "$OUT/data/tickers" "$OUT/data/light"
VITE_STATIC=1 npx vite build --base ./ --outDir "$OUT" --emptyOutDir false >/dev/null
sed -i '1s/<!doctype html>/<!DOCTYPE html>/' "$OUT/index.html"
for f in brief backtest accuracy research ablation universe longterm meta market all journal; do
  [[ -f $ART/$f.json ]] && cp "$ART/$f.json" "$OUT/data/"
done
cp "$ART"/light/*.json "$OUT/data/light/" 2>/dev/null || true
mkdir -p "$OUT/data/journal" "$OUT/data/live"
cp "$ART"/journal/*.json "$OUT/data/journal/" 2>/dev/null || true
# live prices cannot be fetched from a static copy: include the last set captured, with its timestamp
[[ -f $ART/live.json ]] && cp "$ART/live.json" "$OUT/data/live/board.json"
# full model pages: today's ideas, then the most traded names, up to 150 files
python3 - "$ART" "$OUT" <<'PY'
import json, shutil, sys, pathlib
art, out = map(pathlib.Path, sys.argv[1:])
want = []
if (art / "brief.json").exists():
    b = json.loads((art / "brief.json").read_text())
    want += [i["ticker"] for i in b.get("longs", []) + b.get("shorts", [])]
if (art / "all.json").exists():
    a = json.loads((art / "all.json").read_text())
    k = a["cols"].index("dollar_volume")
    want += [r[0] for r in sorted(a["rows"], key=lambda r: -(r[k] or 0))]
n = 0
for t in dict.fromkeys(want):
    src = art / "tickers" / f"{t}.json"
    if src.exists():
        shutil.copy(src, out / "data" / "tickers" / src.name)
        n += 1
        if n >= 150:
            break
print(f"snapshot: {n} full ticker pages, {len(list((out / 'data' / 'light').glob('*.json')))} ticker shards")
PY
du -sh "$OUT" | cut -f1
