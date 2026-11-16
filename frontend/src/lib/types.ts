export interface Headline { time: string; headline: string; source: string; sentiment: number; event: string }
export interface Why { factor: string; key: string; value: number; contribution: number }
export interface TraitModel { trait: number; market_beta: number; gamma: number; effective: number; evidence: number }

export interface Ticket {
  ticker: string; name: string; sector: string; kind: "option" | "stock"; direction: "UP" | "DOWN"; side: string;
  prob: number; mu_z: number; exp_move_pct: number; premarket_gap: number; ref_price: number; iv: number;
  earnings_today: boolean; days_to_earn: number; why: Why[]; headlines: Headline[];
  ticker_model: Record<string, TraitModel>; liquidity: { adv: number; opt_vol: number; spread: number };
  qualifies: boolean; plan: string;
  contract?: string; strike?: number; expiry?: string; premium?: number; contracts?: number; max_loss?: number;
  ev?: number; ev_raw?: number; p_profit?: number; risk_frac?: number; shares?: number; notional?: number; exp_ret?: number;
  outcome?: { stock_move: number; option_ret?: number; stock_ret?: number; correct: boolean };
}

export interface Straddle {
  ticker: string; name: string; sector: string; implied_move: number; hist_move: number | null;
  edge_ratio: number | null; signal: string; iv: number; premium: number; headlines: Headline[];
}

export interface World {
  politics_risk: number | null; policy_uncertainty_z: number | null; geopolitical_risk_z: number | null;
  geopolitical_threats_z: number | null; vix: number | null; vix_chg: number | null; curve_10_2: number | null;
  hy_spread: number | null; hy_spread_chg5: number | null; dgs10_chg5: number | null; spx_ret1: number | null;
  dxy_ret1: number | null; oil_ret1: number | null; gold_ret1: number | null;
}
export interface AblationRow { target?: string; step: string; ic?: number; ic_t?: number; spread_bps?: number; hit_top?: number; added?: number; note?: string; top_new?: string[] }
export interface Ablation { split: string; results: AblationRow[]; market_model?: { features: number; gbm: { hit: number; base_up_rate: number; sharpe: number; days: number }; ridge: { hit: number; sharpe: number } } }
export interface Brief {
  world?: World;
  scan_date: string; generated_for: string; equity: number; universe_size: number; scanned: number;
  market: { premarket_gap: number; news_items: number; regime_vol: number; cal_slope: number };
  options: Ticket[]; stocks: Ticket[]; straddles: Straddle[];
  thresholds: { min_prob: number; min_ev: number }; qualified: { options: number; stocks: number };
  sectors: { sector: string; score: number; news: number; n: number }[];
  movers: { ticker: string; name: string; gap: number; gap_z: number; news: number; sentiment: number; prob_up: number }[];
}

export interface Stats {
  start: number; end: number; total_return: number; cagr: number; sharpe: number; sortino: number; max_drawdown: number;
  vol_annual: number; best_day: number; worst_day: number; days: number; trades?: number; win_rate?: number;
  avg_win?: number; avg_loss?: number; profit_factor?: number; monthly: { year: number; month: number; ret: number }[];
  robustness?: Robustness;
}

export interface Scenario {
  scale: number; p_target: number; p_double: number; p_loss: number; p_ruin: number; p_dd50: number;
  median_end: number; p5_end: number; p95_end: number;
  fan: { day: number; p5: number; p25: number; p50: number; p75: number; p95: number }[];
}
export interface MonteCarlo {
  start: number; target: number; days: number; required_daily: number; hist_mean_daily: number;
  hist_median_daily: number; scenarios: Scenario[];
}

export interface Trade {
  date: string; ticker: string; side: string; strategy: string; conf: number | null; ev: number; strike: number | null;
  entry: number; ret: number; risk_frac: number; pnl: number; stock_move: number | null; hit: number; earnings: number;
  implied_move?: number; hist_move?: number;
}

export interface LongtermBT {
  curve: { date: string; top: number; universe: number; ret_top: number; ret_univ: number; n: number }[];
  stats: { periods: number; cagr_top: number; cagr_universe: number; excess_annual: number; info_ratio: number; hit_rate: number };
}

export interface Backtest {
  period: { start: string; end: string; days: number };
  strategies: Record<string, Stats>;
  curve: { date: string; options: number; stock: number; straddle: number; swing?: number; n: number }[];
  signal: {
    ic: { ic_mean: number; ic_ir: number; hit_rate_all: number; hit_rate_conf60: number | null; auc: number };
    calibration: { pred: number; actual: number; n: number; ret: number }[];
    deciles: { decile: number; ret_bps: number; up_rate: number; n: number }[];
  };
  montecarlo: Record<string, MonteCarlo>;
  longterm: LongtermBT;
  model: { cal_slope: number; importances: { feature: string; importance: number }[]; coefficients: Record<string, number>; features: number };
  ticker_ic: { best: { ticker: string; ic: number }[]; worst: { ticker: string; ic: number }[]; dist: number[]; edges: number[] };
  trades: Trade[]; costs: Record<string, number>; risk: Record<string, number>;
  data_notes?: { level: "warn" | "info"; text: string }[];
}

export interface UniverseRow {
  ticker: string; name: string; sector: string; liquid: boolean; price: number; chg: number; rv20: number; iv30: number;
  adv: number; opt_vol: number; spread: number; days_to_earn: number; news_drift: number | null;
  gap_follow: number | null; ic: number | null; hit: number | null; spark: number[];
}

export interface TickerProfile {
  ticker: string; name: string; sector: string; market_cap: number; liquid: boolean;
  prices: { d: string; o: number; h: number; l: number; c: number; s: number; n: number; e: number }[];
  news: { t: string; h: string; s: number; m: number; e: string; src: string; d: string; on: boolean }[];
  traits: Record<string, number | string>[];
  earnings: { d: string; implied: number; actual: number; signed: number; surprise: number | null }[];
  performance: {
    days?: number; ic?: number; hit_all?: number; hit_conf?: number | null; n_conf?: number; signal_ret_bps?: number;
    trades?: Record<string, { n: number; win: number; pnl: number }>; recent_trades?: Trade[];
  };
  model: Record<string, TraitModel>;
  accuracy?: AccuracyDetail | null;
  stats: {
    rv20: number; iv30: number | null; iv_rank: number | null; adv: number; opt_vol: number; spread: number | null;
    days_to_earn: number; hist_earn_move: number | null;
  };
}

export interface Meta {
  source: string; generated_at: string; data_start: string; data_end: string; tickers: number; news: number;
  universe: number; features: number; runtime_sec: number; config: Record<string, Record<string, number>>;
}

export interface Longterm {
  ranking: { ticker: string; name: string; sector: string; score: number; drivers: { label: string; z: number }[] }[];
  backtest: LongtermBT;
}

export interface AccuracyRow {
  ticker: string; name?: string; sector?: string; kind: string; days: number; liquid_share: number; rel_accuracy: number; z: number;
  up_accuracy: number; up_rate: number; strong_n: number; strong_accuracy: number | null; strong_bps: number | null;
  signal_bps: number; ic: number; acc_5d?: number; z_5d?: number; signal_5d_bps?: number; n_5d?: number;
}
export interface AccuracyDetail extends Omit<AccuracyRow, "ticker" | "kind"> {
  by_year: { year: number; accuracy: number; n: number; bps: number }[];
  curve: { d: string; signal: number; stock: number }[];
  rolling: { d: string; acc: number }[];
}
export interface FundHorizon {
  n: number; accuracy: number; up_rate: number; best_naive: number; edge_vs_naive: number; z_vs_naive: number;
  conf_n: number; conf_accuracy: number | null; signal_ann: number; buyhold_ann: number; signal_sharpe: number;
  buyhold_sharpe: number; by_year: Record<string, number>; curve: { d: string; signal: number; buyhold: number }[];
}
export interface IndexFund { ticker: string; name: string; kind: string; h1: FundHorizon; h5: FundHorizon; latest: { date: string; p_up_1d: number; p_up_5d: number } }
export interface AccuracyData {
  summary: { stocks: number; median_accuracy: number; mean_accuracy: number; liquid_median_accuracy: number; strong_median_accuracy: number;
    above_55: number; below_45: number; significant_good: number; significant_bad: number; expected_by_chance: number; hist: number[]; hist_edges: number[] };
  stocks: AccuracyRow[]; index: { period: { start: string; end: string }; funds: IndexFund[]; method: string } | null;
  period: { start: string; end: string };
}
export interface ResearchVariant { variant: string; hold_days: number; note: string; ic: number; ic_t: number; spread_bps: number; gross_bps_day: number; net_bps_day: number; net_ann: number; sharpe: number; hit_long: number; hit_short: number; by_year: Record<string, number>; days: number }
export interface Research { split: string; purge_sessions: number; train_rows: number; test_rows: number; cost_bps_round_trip: number; book: string; variants: ResearchVariant[]; top_features_5d: { feature: string; importance: number; research: boolean }[] }
export interface Robustness { sharpe: number; psr: number | null; dsr: number | null; sharpe_needed?: number; trials: number }
