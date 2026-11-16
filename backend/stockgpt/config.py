"""Central configuration. Every number that changes trading behaviour lives here."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("STOCKGPT_DATA_DIR", ROOT / "data" / "raw"))
ARTIFACT_DIR = Path(os.getenv("STOCKGPT_ARTIFACT_DIR", ROOT / "artifacts"))


@dataclass
class UniverseConfig:
    """Liquidity gate: only trade names where options fills are realistic."""
    min_price: float = 10.0
    min_avg_dollar_volume: float = 50e6        # 20d avg shares * price
    min_avg_option_volume: int = 5_000          # contracts / day, 20d avg
    max_option_spread_pct: float = 0.06         # ATM bid-ask as % of mid
    lookback_days: int = 20
    max_names: int = 600                        # cap on the liquid universe


@dataclass
class ModelConfig:
    # Walk-forward
    min_train_days: int = 250                   # ~1 trading year before first prediction
    retrain_every_days: int = int(os.getenv("STOCKGPT_RETRAIN_DAYS", "21"))   # 21 = monthly refit
    target: str = os.getenv("STOCKGPT_TARGET", "z_x")    # z_x = stock-relative, z_oc = raw open->close
    max_gbm_rows: int = int(os.getenv("STOCKGPT_MAX_GBM_ROWS", "1500000"))   # most recent rows for the GBMs
    embargo_days: int = 1                       # gap between train end and test start
    # Hierarchical blend: weight on ticker model = n / (n + shrinkage_k)
    shrinkage_k: int = 400
    min_ticker_rows: int = 120
    # Global gradient-boosting model
    gbm_params: dict = field(default_factory=lambda: dict(
        n_estimators=300, learning_rate=0.03, num_leaves=31, min_child_samples=200,
        subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=5.0,
        verbose=-1, n_jobs=4,
    ))


@dataclass
class CostConfig:
    stock_slippage_bps: float = 3.0             # per side
    stock_commission: float = 0.0
    option_commission_per_contract: float = 0.65
    # Fraction of option bid-ask spread paid per side (0.5 = cross to the far side of mid)
    option_spread_capture: float = 0.5


@dataclass
class RiskConfig:
    starting_capital: float = 25_000.0
    kelly_fraction: float = 0.25                # quarter Kelly
    max_risk_per_trade: float = 0.02            # premium at risk as fraction of equity
    max_daily_risk: float = 0.08                # total premium deployed per day
    max_positions: int = 6
    min_edge_prob: float = 0.53                 # min calibrated P(direction correct)
    min_ev: float = 0.03                        # min model EV on premium (3%) after costs
    market_neutral: bool = True                 # stock day trades: equal long and short books
    stock_selection: str = "rank"               # "rank": long top-k / short bottom-k; "threshold": prob + EV gates
    rank_k: int = 10
    stock_leverage: float = 2.0                 # gross intraday exposure for stock day trades
    daily_loss_stop: float = 0.05               # stop trading the day after -5% day


@dataclass
class OptionsConfig:
    days_to_expiry: int = 7                     # weekly options for day trades
    risk_free_rate: float = 0.045
    # Earnings: trade a straddle when predicted |move| exceeds implied move by this ratio
    straddle_edge_ratio: float = 1.15


@dataclass
class LLMConfig:
    """Optional Claude news scoring. Falls back to the lexicon scorer when unset."""
    enabled: bool = os.getenv("STOCKGPT_LLM_SCORING", "0") == "1"
    model: str = os.getenv("STOCKGPT_LLM_MODEL", "claude-opus-5-5")
    headlines_per_request: int = 40


@dataclass
class Settings:
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    options: OptionsConfig = field(default_factory=OptionsConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)


SETTINGS = Settings()
