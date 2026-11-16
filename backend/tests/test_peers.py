"""Peer/competitor features: grouping and point-in-time behaviour."""
import numpy as np
import pandas as pd

from stockgpt.peers import _apply, corr_matrices, graph_matrix, industry_matrix


def test_industry_widens_small_groups():
    tickers = ["A", "B", "C", "D", "E"]
    sic = pd.Series({"A": "3571", "B": "3571", "C": "3572", "D": "3674", "E": "6021"})
    W = industry_matrix(tickers, sic)
    assert np.allclose(W.sum(1)[:3], 1.0)            # A, B (4 digits) and C (widened to 3 digits) find peers
    assert W[2, 0] > 0 and W[3].sum() == 0            # D (3674) has no 35xx peer at any level
    assert W[0, 1] > 0 and W[0, 4] == 0               # bank is never a tech peer
    assert np.all(np.diag(W) == 0)


def test_graph_symmetric_for_competitors():
    rels = pd.DataFrame({"ticker": ["A"], "other_ticker": ["B"], "rel_type": ["competitor"], "confidence": [0.9]})
    W = graph_matrix(["A", "B", "C"], rels, ("competitor",))
    assert W[0, 1] == 1.0 and W[1, 0] == 1.0 and W[2].sum() == 0


def test_corr_peers_use_only_past_window():
    rng = np.random.default_rng(0)
    T, N = 400, 6
    X = rng.normal(size=(T, N))
    X[:, 1] = X[:, 0] + rng.normal(scale=0.1, size=T)            # 0 and 1 move together
    lr = pd.DataFrame(X)
    mats = corr_matrices(lr, k=1, window=252, every=63)
    first_start, W = mats[0]
    assert first_start == 252 and W[0, 1] == 1.0
    # changing the future does not change the first matrix
    X2 = X.copy()
    X2[252:] = rng.normal(size=(T - 252, N))
    W2 = corr_matrices(pd.DataFrame(X2), k=1, window=252, every=63)[0][1]
    assert np.array_equal(W, W2)


def test_apply_ignores_missing():
    W = np.array([[0, 1.0], [1.0, 0]], dtype=np.float32)
    X = np.array([[np.nan, 2.0]])
    assert _apply(W, X)[0, 0] == 2.0 and _apply(W, X)[0, 1] == 0.0
