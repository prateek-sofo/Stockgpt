"""Load the data contract tables from data/raw/, or fall back to the synthetic demo market."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import DATA_DIR
from .schema import TABLES, validate


def _read(path_base: Path) -> pd.DataFrame | None:
    for ext, fn in ((".parquet", pd.read_parquet), (".csv", pd.read_csv)):
        p = path_base.with_suffix(ext)
        if p.exists():
            return fn(p)
    return None


def _normalise(name: str, df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "ticker" in df:
        df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    for col in ("date", "report_date"):
        if col in df:
            df[col] = pd.to_datetime(df[col]).dt.tz_localize(None).dt.normalize()
    if name == "news":
        ts = pd.to_datetime(df["published_at"], utc=True)
        df["published_at"] = ts
    return df


def load_all(data_dir: Path | None = None, demo_if_missing: bool = True) -> tuple[dict, str]:
    """Return (tables, source) where source is 'user' or 'synthetic'."""
    data_dir = Path(data_dir or DATA_DIR)
    tables = {name: _read(data_dir / name) for name in TABLES}
    required = ("tickers", "prices", "news")
    if all(tables[n] is not None for n in required):
        problems = [p for n, df in tables.items() if df is not None for p in validate(n, df)]
        if problems:
            raise ValueError("Data contract violations:\n" + "\n".join(problems))
        out = {n: _normalise(n, df) for n, df in tables.items() if df is not None}
        for n in TABLES:
            out.setdefault(n, pd.DataFrame(columns=TABLES[n]["required"]))
        return out, "user"
    if not demo_if_missing:
        missing = [n for n in required if tables[n] is None]
        raise FileNotFoundError(f"Missing tables in {data_dir}: {missing}")
    from ..config import ARTIFACT_DIR
    cache = ARTIFACT_DIR / "demo_data"
    if cache.exists() and all((cache / f"{n}.parquet").exists() for n in TABLES):
        raw = {n: pd.read_parquet(cache / f"{n}.parquet") for n in TABLES}
    else:
        from .synthetic import generate
        raw = generate()
        cache.mkdir(parents=True, exist_ok=True)
        for n, df in raw.items():
            df.to_parquet(cache / f"{n}.parquet", index=False)
    return {n: _normalise(n, df) for n, df in raw.items()}, "synthetic"
