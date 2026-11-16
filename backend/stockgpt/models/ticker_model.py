"""Hierarchical per-ticker direction + magnitude model.

Each ticker gets its own model, estimated with partial pooling (the standard approach when
each unit has little data):

  expected z-return for ticker i on day t
      mu_it = sum_k beta_k * x_k,it                     (market-wide effect of each signal)
            + sum_j gamma_j * trait_ij(t) * x_j,it      (ticker i's own sensitivity)
            + nonlinear GBM term

trait_ij(t) is ticker i's own historical slope on signal j, shrunk toward the market slope
and computed only from days before t (see features.add_ticker_traits). So each ticker's
effective coefficient on news is beta_news + gamma_news * trait_i,news: a ticker that has
historically kept drifting after news gets a larger coefficient, one that fades news gets a
smaller or negative one. Tickers with little history default to the market behaviour.

Outputs, all in units of the ticker's own daily vol (z):
  mu      expected open-to-close return
  exp_absz expected |open-to-close| move (magnitude model)
  p_up    P(close > open) = Phi(cal * mu / s), s implied by exp_absz
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.linear_model import Ridge

from ..config import SETTINGS
from ..features import LINEAR_FEATURES, TRAITS

warnings.filterwarnings("ignore", category=UserWarning)

GBM_WEIGHT = 1.0      # research holdout: trees alone beat the ridge blend; ridge kept for per-ticker coefficients


@dataclass
class HierarchicalModel:
    features: list[str]
    linear_features: list[str] = field(default_factory=lambda: list(LINEAR_FEATURES))
    ridge: Ridge | None = None
    lin_mu: np.ndarray | None = None
    lin_sd: np.ndarray | None = None
    gbm: lgb.LGBMRegressor | None = None
    mag: lgb.LGBMRegressor | None = None
    gbm5: lgb.LGBMRegressor | None = None
    gbms: list | None = None
    cal_slope: float = 1.0          # fitted on earlier out-of-sample predictions

    # ------------------------------------------------------------------ fit
    def fit(self, df: pd.DataFrame) -> "HierarchicalModel":
        tgt = SETTINGS.model.target if SETTINGS.model.target in df else "z_oc"
        df = df[df[tgt].notna()]
        self.linear_features = [f for f in self.linear_features if f in df]
        XL = df[self.linear_features].fillna(0.0).values
        self.lin_mu, self.lin_sd = XL.mean(0), XL.std(0) + 1e-9
        y = df[tgt].values
        self.ridge = Ridge(alpha=50.0).fit((XL - self.lin_mu) / self.lin_sd, y)
        # Gradient boosting on the most recent rows only (keeps refits fast on the full universe;
        # the ridge core above still sees every row)
        cap = SETTINGS.model.max_gbm_rows
        if len(df) > cap:
            df = df.sort_index(level="date").iloc[-cap:]
            y = df[tgt].values
        X = df[self.features].astype(np.float32)
        p = dict(SETTINGS.model.gbm_params)
        cons = {**p, "num_leaves": 15, "min_child_samples": 1000, "n_estimators": 400, "learning_rate": 0.02}
        # seed ensemble: the research holdout showed averaging models with different seeds lifts Sharpe
        self.gbms = [lgb.LGBMRegressor(objective="huber", alpha=1.5, **{**cons, "random_state": seed}).fit(X, y)
                     for seed in range(SETTINGS.model.gbm_seeds)]
        self.gbm = self.gbms[0]
        self.mag = lgb.LGBMRegressor(objective="regression", **{**p, "n_estimators": 200}).fit(
            X, np.abs(y))
        # 5-session model (open t -> close t+4, market-relative): slower signal, a fifth of the turnover
        self.gbm5 = None
        if "z5_x" in df:
            ok = df["z5_x"].notna().values
            if ok.sum() > 10_000:
                self.gbm5 = lgb.LGBMRegressor(objective="huber", alpha=1.5, **cons).fit(X[ok], df["z5_x"].values[ok])
        return self

    def calibrate(self, mu: np.ndarray, z: np.ndarray) -> float:
        """Slope of realised z on predicted mu (through the origin), on prior OOS rows."""
        ok = np.isfinite(mu) & np.isfinite(z)
        mu, z = mu[ok], z[ok]
        if len(mu) < 2000 or np.dot(mu, mu) < 1e-9:
            return self.cal_slope
        self.cal_slope = float(np.clip(np.dot(mu, z) / np.dot(mu, mu), 0.0, 2.0))
        return self.cal_slope

    # -------------------------------------------------------------- predict
    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        XL = (df[self.linear_features].fillna(0.0).values - self.lin_mu) / self.lin_sd
        mu_lin = self.ridge.predict(XL)
        X = df[self.features].astype(np.float32)
        models = getattr(self, "gbms", None) or [self.gbm]
        mu_gbm = np.mean([m.predict(X) for m in models], axis=0)
        mu = (1 - GBM_WEIGHT) * mu_lin + GBM_WEIGHT * mu_gbm
        absz = np.clip(self.mag.predict(X), 0.3, 8)
        s = absz * np.sqrt(np.pi / 2)
        p_raw = norm.cdf(mu / s)
        p_cal = norm.cdf(self.cal_slope * mu / s)
        out = pd.DataFrame({"mu": mu, "mu_lin": mu_lin, "mu_gbm": mu_gbm, "p_up": p_raw,
                            "p_cal": np.clip(p_cal, 0.02, 0.98), "exp_absz": absz}, index=df.index)
        if getattr(self, "gbm5", None) is not None:
            out["mu5"] = self.gbm5.predict(X)
        return out

    # ------------------------------------------------------------ explain
    def coefficients(self) -> dict[str, float]:
        """Linear coefficients in raw feature units (z-return per unit of feature)."""
        return {f: float(c / sd) for f, c, sd in zip(self.linear_features, self.ridge.coef_, self.lin_sd)}

    def importances(self, top: int = 20) -> list[dict]:
        imp = pd.Series(self.gbm.booster_.feature_importance("gain"), index=self.features)
        imp = (imp / imp.sum()).sort_values(ascending=False).head(top)
        return [{"feature": k, "importance": float(v)} for k, v in imp.items()]

    def ticker_model(self, row: pd.Series) -> dict:
        """This ticker's own effective coefficients on each behavioural signal.

        effective_j = (1-w_gbm) * (beta_j + gamma_j * trait_ij)
        """
        coef = self.coefficients()
        out = {}
        for f in TRAITS:
            if f not in coef:
                continue
            trait = float(row.get(f"tr_{f}", np.nan))
            beta, gamma = coef.get(f, 0.0), coef.get(f"tx_{f}", 0.0)
            out[f] = dict(trait=trait, market_beta=beta, gamma=gamma,
                          effective=(beta + gamma * (trait if np.isfinite(trait) else 0.0)),
                          evidence=float(row.get(f"n_{f}", 0.0)))
        return out
