"""Competitor and peer features: what happened to a company's competitors before today's open.

Peer sets (each a row-normalised N x N weight matrix):
  * graph:   Sofo World relationships (competitor, customer, supplier, partner) mapped to tickers.
             The graph is today's; its *membership* is not point-in-time (stated in the backtest notes),
             every value flowing through it is.
  * industry: same 4-digit SEC SIC code, widened to 3 then 2 digits when a group has < 3 members.
  * corr:    the 10 tickers whose daily returns correlated most with this one over the prior 252 days,
             re-estimated every 63 trading days from past data only.

Features (all known at 09:00 ET on day t):
  peer_<set>_news       peers' overnight news sentiment (magnitude-weighted)
  peer_<set>_ret1       peers' return on t-1 (lead-lag / industry momentum)
  peer_<set>_earn_surp  EPS surprise of peers reporting since the last close (BMO today, AMC yesterday):
                        intra-industry information transfer
  peer_<set>_earn_react peers' reaction-day return on t-1 for peers that reported then
  peer_<set>_events     count of peers' material events overnight (from Sofo World events)
  supply_news           news of the company's customers and suppliers (graph only)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import DATA_DIR


def _normalise(W: np.ndarray) -> np.ndarray:
    np.fill_diagonal(W, 0.0)
    s = W.sum(1, keepdims=True)
    return np.divide(W, s, out=np.zeros_like(W), where=s > 0)


def industry_matrix(tickers: list[str], sic: pd.Series) -> np.ndarray:
    codes = sic.reindex(tickers).fillna("").astype(str).str.zfill(4).values
    n = len(tickers)
    W = np.zeros((n, n), dtype=np.float32)
    for digits in (4, 3, 2):
        key = np.array([c[:digits] if c.strip("0") else "" for c in codes])
        need = W.sum(1) < 3                      # rows that still have too few peers
        for k in np.unique(key[need]):
            if not k:
                continue
            members = np.where(key == k)[0]
            if len(members) < 2:
                continue
            rows = members[need[members]]
            W[np.ix_(rows, members)] = 1.0
    return _normalise(W)


def graph_matrix(tickers: list[str], rels: pd.DataFrame | None, types: tuple[str, ...]) -> np.ndarray | None:
    if rels is None or rels.empty:
        return None
    r = rels[rels["rel_type"].isin(types)]
    pos = {t: i for i, t in enumerate(tickers)}
    W = np.zeros((len(tickers), len(tickers)), dtype=np.float32)
    for a, b, c in zip(r["ticker"], r["other_ticker"], r["confidence"].fillna(0.5)):
        i, j = pos.get(a), pos.get(b)
        if i is not None and j is not None:
            W[i, j] = max(W[i, j], c)
            if r is not None and "competitor" in types:      # competition is symmetric
                W[j, i] = max(W[j, i], c)
    return _normalise(W) if W.any() else None


def corr_matrices(lr: pd.DataFrame, k: int = 10, window: int = 252, every: int = 63) -> list[tuple[int, np.ndarray]]:
    """[(first row index it applies to, W)], each W estimated from the `window` days before it."""
    X = lr.values.astype(np.float64)
    T, N = X.shape
    out = []
    for start in range(window, T, every):
        hist = X[start - window:start]
        ok = np.isfinite(hist).mean(0) > 0.8
        h = np.where(np.isfinite(hist), hist, 0.0)
        h = (h - h.mean(0)) / (h.std(0) + 1e-12)
        C = (h.T @ h) / len(h)
        C[~ok, :] = 0
        C[:, ~ok] = 0
        np.fill_diagonal(C, -np.inf)
        W = np.zeros((N, N), dtype=np.float32)
        top = np.argpartition(-C, k, axis=1)[:, :k]
        rows = np.repeat(np.arange(N), k)
        cols = top.ravel()
        good = np.isfinite(C[rows, cols]) & (C[rows, cols] > 0.2) & ok[rows]
        W[rows[good], cols[good]] = 1.0
        out.append((start, _normalise(W)))
    return out


def _apply(W, X: np.ndarray) -> np.ndarray:
    """peer average of X (T x N) under weights W (N x N); NaN treated as 'no information' (0)."""
    Xz = np.nan_to_num(X, nan=0.0)
    return Xz @ W.T


def _apply_time_varying(mats, X: np.ndarray) -> np.ndarray:
    out = np.zeros_like(X, dtype=np.float32)
    Xz = np.nan_to_num(X, nan=0.0)
    bounds = [s for s, _ in mats] + [len(X)]
    for (s, W), e in zip(mats, bounds[1:]):
        out[s:e] = Xz[s:e] @ W.T
    return out


def load_relationships() -> pd.DataFrame | None:
    p = DATA_DIR / "sofo_relationships.parquet"
    return pd.read_parquet(p) if p.exists() else None


def peer_features(dates, tickers, lr: pd.DataFrame, panel: pd.DataFrame, tables: dict) -> pd.DataFrame:
    """Returns a (date, ticker)-indexed frame of peer features aligned to `panel`."""
    tickers = list(tickers)
    W_ind = industry_matrix(tickers, tables["tickers"].set_index("ticker").get("sic", pd.Series(dtype=str)))
    rels = load_relationships()
    W_comp = graph_matrix(tickers, rels, ("competitor",))
    W_supply = graph_matrix(tickers, rels, ("customer", "supplier"))
    corr = corr_matrices(lr)

    def wide(col):
        if col not in panel:
            return np.zeros((len(dates), len(tickers)), dtype=np.float32)
        return panel[col].unstack().reindex(index=dates, columns=tickers).values.astype(np.float32)

    news = wide("news_on_wsent")
    ret1 = lr.shift(1).reindex(index=dates, columns=tickers).values           # t-1 return, known at t
    earn_today = wide("is_earnings")                                          # BMO today / AMC yesterday
    surp = np.where(earn_today > 0, wide("eps_surprise"), np.nan)
    react_prev = np.vstack([np.full((1, len(tickers)), np.nan), np.where(earn_today[:-1] > 0, ret1[1:], np.nan)])
    events = wide("news_on_count") if "news_on_count" in panel else np.zeros_like(news)

    feats = {}
    sets = {"ind": W_ind, "comp": W_comp}
    for name, W in sets.items():
        if W is None:
            continue
        feats[f"peer_{name}_news"] = _apply(W, news)
        feats[f"peer_{name}_ret1"] = _apply(W, ret1)
        feats[f"peer_{name}_earn_surp"] = _apply(W, surp)
        feats[f"peer_{name}_earn_react"] = _apply(W, react_prev)
        feats[f"peer_{name}_events"] = _apply(W, (events > 0).astype(np.float32))
    if corr:
        feats["peer_corr_news"] = _apply_time_varying(corr, news)
        feats["peer_corr_ret1"] = _apply_time_varying(corr, ret1)
        feats["peer_corr_earn_surp"] = _apply_time_varying(corr, surp)
        feats["peer_corr_earn_react"] = _apply_time_varying(corr, react_prev)
    if W_supply is not None:
        feats["supply_news"] = _apply(W_supply, news)
        feats["supply_ret1"] = _apply(W_supply, ret1)

    idx = pd.MultiIndex.from_product([dates, tickers], names=["date", "ticker"])
    df = pd.DataFrame({k: v.reshape(-1) for k, v in feats.items()}, index=idx)
    return df.reindex(panel.index).astype(np.float32)
