"""HTTP API + static UI.

    uvicorn stockgpt.api:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import ARTIFACT_DIR, ROOT

app = FastAPI(title="StockGPT", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_job = {"running": False, "log": "", "mode": None}


def _load(name: str):
    p = ARTIFACT_DIR / name
    if not p.exists():
        raise HTTPException(404, f"{name} not built yet: run `python -m stockgpt.pipeline`")
    return json.loads(p.read_text())


@app.get("/api/health")
def health():
    return {"ok": True, "artifacts": (ARTIFACT_DIR / "meta.json").exists()}


@app.get("/api/meta")
def meta():
    return _load("meta.json")


@app.get("/api/brief")
def brief():
    return _load("brief.json")


@app.get("/api/backtest")
def backtest():
    return _load("backtest.json")


@app.get("/api/ablation")
def ablation():
    return _load("ablation.json")


@app.get("/api/universe")
def universe():
    return _load("universe.json")


@app.get("/api/longterm")
def longterm():
    return _load("longterm.json")


@app.get("/api/ticker/{symbol}")
def ticker(symbol: str):
    if not re.fullmatch(r"[A-Za-z0-9.\-]{1,10}", symbol):
        raise HTTPException(400, "bad symbol")
    return _load(f"tickers/{symbol.upper()}.json")


@app.post("/api/run")
def run(mode: str = "scan"):
    """Kick off the pipeline. mode=scan (daily, ~30s) or mode=full (retrain + backtest)."""
    if _job["running"]:
        return {"started": False, "reason": "already running"}
    args = [sys.executable, "-m", "stockgpt.pipeline"] + (["--scan"] if mode == "scan" else [])

    def work():
        _job.update(running=True, log="", mode=mode)
        proc = subprocess.run(args, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        _job.update(running=False, log=(proc.stdout + proc.stderr)[-4000:])

    threading.Thread(target=work, daemon=True).start()
    return {"started": True, "mode": mode}


@app.get("/api/run")
def run_status():
    return _job


DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST / path
        if path and f.is_file() and DIST in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
