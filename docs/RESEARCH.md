# What the research says, and what StockGPT takes from it

The goal is next-session (and next-week) direction for US stocks from news, events, competitors and
fundamentals. This problem has a large literature and several mature open-source frameworks. This file
maps the main findings to what is implemented, with the file where each lives.

## 1. How much is predictable

| Finding | Source | Consequence here |
|---|---|---|
| Daily cross-sectional rank IC of 0.03-0.05 is state of the art for price/volume models, and that is on Chinese A-shares, a less efficient market | Microsoft Qlib benchmarks (Alpha158 + LightGBM: IC ~0.045, rank IC ~0.047 on CSI300) | Our holdout IC of ~0.02 on liquid US names is in the expected range; an IC of 0.1 on real data would indicate a leak |
| Machine learning beats linear factor models mostly through nonlinear interactions; the gains come from momentum, liquidity and volatility signals; trees and shallow nets work best | Gu, Kelly, Xiu (2020), *Empirical Asset Pricing via Machine Learning*, RFS | Gradient-boosted trees with conservative settings + a ridge core; features rank-normalised per day (their preprocessing) |
| Most published anomalies shrink ~58% after publication and are weakest in large liquid stocks | McLean, Pontiff (2016), JF | Expect the public price signals to be weak in the liquid universe we trade; our own news is the differentiator |
| With hundreds of tested signals a t-stat of 2 is not enough; use t > 3 | Harvey, Liu, Zhu (2016), RFS | The research table reports t-stats; only t > 3 is treated as real |
| A backtest chosen as the best of N trials overstates its Sharpe; correct with the Deflated Sharpe Ratio; use purged, embargoed walk-forward | Bailey, López de Prado (2014); López de Prado (2018), *Advances in Financial ML* | `backtest.deflated_sharpe`; walk-forward with purge = label horizon; ablation holdout |

## 2. Signals that survive in the literature (implemented in `features.py`)

| Signal | Source | Feature |
|---|---|---|
| Intraday returns persist, overnight returns persist, and they reverse each other, for years | Lou, Polk, Skouras (2019), JFE, *A Tug of War* | `intra_5/20/60`, `over_5/20/60` (directly relevant: our target is open-to-close) |
| 12-month momentum, skipping the last month | Jegadeesh, Titman (1993) | `mom_12_1`, `mom_6_1` |
| One-month and one-week reversal | Jegadeesh (1990); Lehmann (1990) | `rev_1m`, `ret_5d_z`, `ret_1d_z` |
| Post-earnings-announcement drift | Bernard, Thomas (1989) | `eps_surprise`, per-ticker `tr_eps_surprise`, `days_since_earn` |
| Earnings announcement premium | Frazzini, Lamont (2007); Barber et al. (2013) | `days_to_earn`, `is_earnings` |
| Lottery stocks (high max daily return) underperform | Bali, Cakici, Whitelaw (2011) | `max_20`, `min_20` |
| Nearness to the 52-week high | George, Hwang (2004) | `dist_52w_high` |
| Idiosyncratic volatility and beta anomalies | Ang et al. (2006); Frazzini, Pedersen (2014) | `ivol_60`, `beta_60` |
| Illiquidity premium | Amihud (2002) | `amihud_20`, `turn_5_60` |
| Industry momentum and lead-lag between related firms | Moskowitz, Grinblatt (1999); Hou (2007) | `peer_ind_*`, `peer_corr_*` (`peers.py`) |
| Customer-supplier news travels slowly | Cohen, Frazzini (2008), *Economic Links and Predictable Returns* | `supply_news` from Sofo World's customer/supplier graph |
| Intra-industry information transfer at earnings | Foster (1981); Thomas, Zhang (2008) | `peer_*_earn_surp`, `peer_*_earn_react` |
| Price-volume technical features | Qlib Alpha158; Kakushadze (2016), *101 Formulaic Alphas* | `corr_pv_20`, `rsv_20`, `klen_5`, `vol_5_60` |

## 3. News and text (the part that needs Sofo World)

| Finding | Source | Consequence here |
|---|---|---|
| Media pessimism predicts next-day returns; firm-specific negative words predict earnings and returns | Tetlock (2007); Tetlock, Saar-Tsechansky, Macskassy (2008) | Overnight sentiment features; magnitude weighting |
| A supervised sentiment model on Dow Jones news gives a daily long-short Sharpe above 4 before costs; fresh news is priced within a day or two | Ke, Kelly, Xiu (2019), *Predicting Returns with Text Data* | Trade the open after overnight news; `news_abnormal` (novelty); hold 1 day |
| LLM embeddings of news beat dictionary sentiment in 16 markets | Chen, Kelly, Xiu (2022), *Expected Returns and Large Language Models* | `nlp/sentiment.py` ClaudeScorer (structured sentiment, materiality, event type) |
| GPT scores of headlines predict next-day returns; strongest in small caps and negative news; the newest version reports ~90% hit rate on the *initial* reaction, with drift afterwards | Lopez-Lira, Tang (2023-2025) | Same design: score headlines before the open; our backtest measures what is left after the open |
| News about economically linked firms is incorporated with a lag | Cohen, Frazzini (2008); Scherbina, Schlusche (2015) | Competitor and supply-chain news features |

## 4. Markets and macro

| Finding | Source | Consequence here |
|---|---|---|
| Market direction is barely predictable out of sample from macro variables | Welch, Goyal (2008), RFS | Index-fund page reports accuracy next to the naive "always up" rate; macro/political features are context |
| Trend filters (price vs 10-month average) cut drawdowns more than they add return | Faber (2007) | `above_200d` in the index-fund model |
| Policy uncertainty and geopolitical risk are measurable from newspapers | Baker, Bloom, Davis (2016); Caldara, Iacoviello (2022) | `sources/macro.py`; shown on the brief |
| Buying options earns a negative risk premium on average (implied > realised vol) | Coval, Shumway (2001); Bakshi, Kapadia (2003) | Option trades require calibrated EV above costs; most days the answer is no trade |
| Straddles before earnings have earned positive returns | Gao, Xing, Zhang (2018), JFQA | Earnings-straddle strategy, enabled once real IV history exists |

## 5. Open-source frameworks consulted

* **Qlib** (Microsoft): Alpha158/Alpha360 feature sets, LightGBM baseline, IC / rank-IC evaluation, top-k/drop-n portfolio. We use the same evaluation (IC, rank IC, top-k long/short) and part of Alpha158.
* **alphalens** (Quantopian): factor tear sheets: IC by horizon, quantile returns, turnover. Our decile and by-year tables follow it.
* **mlfinlab / *Advances in Financial ML***: purged k-fold, embargo, deflated Sharpe, meta-labeling.
* **Numerai**: era-wise (per-day) evaluation and rank targets; feature neutralisation.
* **FinBERT / FinGPT**: finance-tuned language models for sentiment; our scorer interface accepts any of them.
* **vectorbt / zipline / backtrader**: event-driven backtesting; we simulate at the open/close with explicit costs.

## 6. What this implies for trading

1. The edge from public price data is small in liquid US stocks. Expect IC 0.01-0.03, and expect it to pay only when turnover is low (multi-day holds) or costs are tiny.
2. The largest documented daily edge is fresh, firm-specific news scored well and traded at the next open. That is the Sofo World input.
3. Rank stocks against each other and trade a balanced long/short book; do not try to call the market.
4. Options amplify a directional edge but charge a volatility premium and a spread; use them only when the calibrated expected value clears costs (earnings and large news days).
5. Judge every result after costs, by year, and against the number of things tried.
