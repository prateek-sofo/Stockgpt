"""Core correctness tests: no look-ahead, option math, sentiment, end-to-end smoke."""
import numpy as np
import pandas as pd
import pytest

from stockgpt.data.synthetic import generate
from stockgpt.features import _assign_sessions, add_ticker_traits, build_panel, feature_columns
from stockgpt.nlp.sentiment import LexiconScorer
from stockgpt.options import bs_price, day_trade_return, kelly_fraction


@pytest.fixture(scope="module")
def small():
    t = generate(n_tickers=25, start="2024-01-01", end="2025-06-30", seed=3)
    return t, build_panel(t)


def test_sentiment_direction():
    s = LexiconScorer()
    assert s.score_one("Acme raises full-year guidance well above consensus")[0] > 0.5
    assert s.score_one("Acme cuts full-year guidance, shares plunge")[0] < -0.5
    assert s.score_one("Acme to present at upcoming industry conference")[1] == 0.0


def test_news_session_assignment():
    dates = pd.bdate_range("2025-03-03", "2025-03-10")  # Mon..Mon
    news = pd.DataFrame({
        "published_at": pd.to_datetime([
            "2025-03-04 08:30", "2025-03-04 09:30", "2025-03-04 17:00", "2025-03-07 18:00",
        ]).tz_localize("America/New_York").tz_convert("UTC"),
        "ticker": "X", "headline": "h"})
    out = _assign_sessions(news, dates)
    got = out["date"].dt.strftime("%Y-%m-%d").tolist()
    # 08:30 Tue -> Tue scan; 09:30 Tue -> Wed; 17:00 Tue -> Wed; Fri 18:00 -> Mon
    assert got == ["2025-03-04", "2025-03-05", "2025-03-05", "2025-03-10"]
    assert out["overnight"].tolist() == [True, False, True, True]


def test_features_do_not_see_future(small):
    """Changing a future outcome must not change any feature on earlier dates."""
    _, panel = small
    feats = feature_columns(panel)
    d = panel.index.get_level_values("date").unique()
    cut = d[200]
    p2 = panel.copy()
    future = p2.index.get_level_values("date") >= cut
    p2.loc[future, "z_oc"] = -p2.loc[future, "z_oc"] * 3       # perturb outcomes from cut onward
    for c in [c for c in p2.columns if c.startswith(("tr_", "tx_", "n_"))]:
        del p2[c]
    p2 = add_ticker_traits(p2)
    past = panel.index.get_level_values("date") <= cut           # traits at `cut` use outcomes < cut
    a = panel.loc[past, [f for f in feats if f.startswith(("tr_", "tx_"))]]
    b = p2.loc[past, a.columns]
    pd.testing.assert_frame_equal(a, b)


def test_targets_not_in_features(small):
    _, panel = small
    feats = feature_columns(panel)
    for t in ("r_oc", "z_oc", "y_dir", "close_px", "r_fwd5", "ivw_t"):
        assert t not in feats


def test_put_call_parity():
    S, K, T, s, r = 100.0, 105.0, 0.1, 0.3, 0.04
    c = bs_price(S, K, T, s, r, True)
    p = bs_price(S, K, T, s, r, False)
    assert abs((c - p) - (S - K * np.exp(-r * T))) < 1e-8


def test_option_round_trip_costs_money_when_flat():
    ret, *_ = day_trade_return(np.array([100.0]), np.array([100.0]), 0.4, 0.4, np.array([True]), 0.03)
    assert ret[0] < 0  # theta + spread


def test_kelly_bounds():
    assert kelly_fraction(np.array([0.5]), np.array([1.0]), np.array([-1.0]))[0] == 0
    assert 0 < kelly_fraction(np.array([0.6]), np.array([1.0]), np.array([-1.0]))[0] <= 1


def test_model_fit_predict(small):
    from stockgpt.models.ticker_model import HierarchicalModel
    _, panel = small
    feats = feature_columns(panel)
    d = panel.index.get_level_values("date")
    cut = d.unique()[250]
    m = HierarchicalModel(feats).fit(panel[d < cut])
    out = m.predict(panel[d >= cut])
    assert out["p_cal"].between(0, 1).all()
    assert (out["exp_absz"] > 0).all()
