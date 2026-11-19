import clsx from "clsx";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis,
} from "recharts";
import { Card, chartTooltip, ErrorBox, Loading, PageHeader, Pill, Stat, Tabs } from "../components/ui";
import { useApi } from "../lib/api";
import { compact, MONTHS, num, pct, tone, usd } from "../lib/format";
import type { Ablation, Backtest as BT, Research } from "../lib/types";

const STRATS = {
  swing_5d: { col: "swing", name: "Swing 5-day long/short", color: "var(--color-up)", desc: "Each day buy the 25 strongest and short the 25 weakest liquid names by the 5-session model; hold 5 sessions. 1× long, 1× short." },
  stock_daytrade: { col: "stock", name: "Day-trade long/short", color: "var(--color-sky)", desc: "Each day long the 25 strongest and short the 25 weakest by the 1-day model, open to close. 1× long, 1× short." },
  options_daytrade: { col: "options", name: "Options day trade", color: "var(--color-accent)", desc: "Weekly calls/puts bought at the open, sold at the close. Quarter-Kelly, ≤2% premium per trade, ≤8% per day." },
  earnings_straddle: { col: "straddle", name: "Earnings straddle", color: "var(--color-amber)", desc: "Buy the straddle at the close before a report when the ticker's own history of moves beats the implied move by 15%+." },
} as const;
type K = keyof typeof STRATS;

export default function Performance() {
  const { data, error } = useApi<BT>("backtest");
  const [k, setK] = useState<K>("stock_daytrade");
  const { data: research } = useApi<Research>("research");
  const [log, setLog] = useState(true);
  const curve = useMemo(() => {
    if (!data) return [];
    let peak = 0;
    const col = STRATS[k].col;
    return data.curve.map((r) => {
      const v = (r[col as "stock"] ?? data.risk.starting_capital) as number;
      peak = Math.max(peak, v);
      return { ...r, v, dd: v / peak - 1 };
    });
  }, [data, k]);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const s = data.strategies[k] ?? data.strategies.stock_daytrade;
  const sig = data.signal;
  const trades = data.trades.filter((t) => t.strategy === k).slice(-60).reverse();
  return (
    <div>
      <PageHeader title="Performance"
        sub={`What each way of trading the model would have earned from ${data.period.start} to ${data.period.end}, on days the model had never seen, after trading costs.`} />

      <section className="card overflow-hidden">
        <table className="w-full text-[14px]">
          <thead><tr className="text-left text-[13.5px] text-ink-400">
            <th className="px-5 py-3 font-medium">Strategy</th><th className="font-medium">Verdict</th>
            <th className="px-3 text-right font-medium">Per year</th><th className="px-3 text-right font-medium">Worst drop</th>
            <th className="px-3 text-right font-medium" title="Sharpe ratio: return per unit of risk. Above 1 is good.">Return for risk</th>
            <th className="px-5 text-right font-medium" title="Chance the edge is real after correcting for the number of variants tried (deflated Sharpe). 95% is the usual bar.">Chance the edge is real</th>
          </tr></thead>
          <tbody>{(Object.keys(STRATS) as K[]).filter((x) => data.strategies[x]).map((x) => {
            const r = data.strategies[x]; const d = r.robustness?.dsr; const traded = (r.trades ?? 0) > 0;
            const verdict = !traded ? { c: "ink" as const, t: "Not tested yet" } : d != null && d >= 0.95 ? { c: "up" as const, t: "Works" }
              : r.cagr > 0 && d != null && d >= 0.5 ? { c: "amber" as const, t: "Promising" } : r.cagr > 0 ? { c: "amber" as const, t: "Unproven" } : { c: "down" as const, t: "Loses money" };
            return (
              <tr key={x} onClick={() => setK(x)} className={clsx("cursor-pointer border-t border-ink-700 hover:bg-ink-800", k === x && "bg-ink-800")}>
                <td className="px-5 py-3"><span className="font-semibold text-ink-100">{STRATS[x].name}</span><div className="max-w-[60ch] text-[13px] text-ink-400">{STRATS[x].desc}</div></td>
                <td><Pill color={verdict.c}>{verdict.t}</Pill></td>
                <td className={clsx("num px-3 text-right", traded ? tone(r.cagr) : "text-ink-500")}>{traded ? pct(r.cagr, 0) : "–"}</td>
                <td className="num px-3 text-right">{traded ? pct(r.max_drawdown, 0) : "–"}</td>
                <td className="num px-3 text-right">{traded ? num(r.sharpe, 2) : "–"}</td>
                <td className="num px-5 text-right">{d == null ? "–" : pct(d, 0, false)}</td>
              </tr>);
          })}</tbody>
        </table>
      </section>
      <p className="mb-4 mt-2 text-[13px] text-ink-400">Click a strategy to see its detail below.</p>

      {!!data.data_notes?.length && (
        <details className="mb-4">
          <summary className="card flex items-center gap-3 px-4 py-2.5 text-[13.5px] text-ink-300 hover:border-ink-600">
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber" />
            {data.data_notes.length} things limit these results: {data.data_notes.filter((n) => n.level === "warn").length} data gaps and {data.data_notes.filter((n) => n.level !== "warn").length} caveats
            <span className="ml-auto text-ink-400">show</span>
          </summary>
          <ul className="card mt-2 space-y-2 px-5 py-4 text-[13.5px] text-ink-300">
            {data.data_notes.map((n, i) => <li key={i} className="flex gap-2.5"><span className={clsx("mt-2 h-1.5 w-1.5 shrink-0 rounded-full", n.level === "warn" ? "bg-amber" : "bg-ink-500")} />{n.text}</li>)}
          </ul>
        </details>)}
      {k === "earnings_straddle" && !s.trades && (
        <div className="card mb-4 px-5 py-4 text-[13px] text-ink-300">No straddle trades: this strategy needs historical options prices to be measured.</div>)}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4 xl:grid-cols-8">
        <Stat label="Total return" value={<span className={tone(s.total_return)}>{pct(s.total_return, 0)}</span>} sub={`${usd(s.start)} → ${usd(s.end)}`} />
        <Stat label="CAGR" value={<span className={tone(s.cagr)}>{pct(s.cagr, 1)}</span>} />
        <Stat label="Sharpe" value={num(s.sharpe, 2)} sub={`Sortino ${num(s.sortino, 2)}`} />
        <Stat label="Max drawdown" value={<span className="text-down">{pct(s.max_drawdown, 1)}</span>} />
        <Stat label="Win rate" value={pct(s.win_rate, 1, false)} sub={`${s.trades ?? 0} trades`} />
        <Stat label="Avg win / loss" value={<span className="text-[17px]"><span className="text-up">{pct(s.avg_win, 1)}</span> <span className="text-ink-500">/</span> <span className="text-down">{pct(s.avg_loss, 1)}</span></span>} sub={k === "stock_daytrade" ? "per position" : "on premium"} />
        <Stat label="Profit factor" value={num(s.profit_factor, 2)} valueClass={s.profit_factor! >= 1 ? "text-up" : "text-down"} />
        {s.robustness?.dsr != null && <Stat label="Deflated Sharpe" value={pct(s.robustness.dsr, 0, false)}
          valueClass={s.robustness.dsr > 0.95 ? "text-up" : s.robustness.dsr > 0.5 ? "text-amber" : "text-down"}
          sub={`P(real edge) after ${s.robustness.trials} trials`}
          hint="Bailey & López de Prado: probability the true Sharpe is above zero after correcting for having tried several variants and for fat tails. Above 95% is the usual bar." />}
        <Stat label="Volatility" value={pct(s.vol_annual, 0, false)} sub={`best ${pct(s.best_day, 1)} · worst ${pct(s.worst_day, 1)}`} />
      </div>

      <Card className="mt-4" title={<span className="flex items-center gap-2"><span className="h-2 w-2 rounded-full" style={{ background: STRATS[k].color }} />Equity curve: {STRATS[k].name}</span>}
        right={<div className="flex items-center gap-3"><span className="hidden text-[12.5px] text-ink-500 lg:block">{STRATS[k].desc}</span>
          <Tabs value={log ? "log" : "lin"} onChange={(v) => setLog(v === "log")} items={[{ v: "lin", label: "Linear" }, { v: "log", label: "Log" }]} /></div>}>
        <div className="h-[320px]">
          <ResponsiveContainer>
            <AreaChart data={curve} margin={{ left: 8, right: 8, top: 8 }}>
              <defs><linearGradient id="eq" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={STRATS[k].color} stopOpacity={0.35} /><stop offset="1" stopColor={STRATS[k].color} stopOpacity={0} /></linearGradient></defs>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="date" tickLine={false} axisLine={false} minTickGap={60} />
              <YAxis scale={log ? "log" : "auto"} domain={["auto", "auto"]} tickFormatter={(v) => compact(v, "$")} tickLine={false} axisLine={false} width={60} />
              <Tooltip {...chartTooltip} formatter={(v: number) => [usd(v), "Equity"]} />
              <ReferenceLine y={data.risk.starting_capital} stroke="var(--color-ink-500)" strokeDasharray="4 4" />
              <Area type="monotone" dataKey="v" stroke={STRATS[k].color} strokeWidth={2} fill="url(#eq)" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        <div className="mt-2 h-[90px]">
          <ResponsiveContainer>
            <AreaChart data={curve} margin={{ left: 8, right: 8 }}>
              <XAxis dataKey="date" hide />
              <YAxis tickFormatter={(v) => pct(v, 0)} tickLine={false} axisLine={false} width={60} />
              <Tooltip {...chartTooltip} formatter={(v: number) => [pct(v, 1), "Drawdown"]} />
              <Area type="monotone" dataKey="dd" stroke="var(--color-down)" fill="var(--color-down)" fillOpacity={0.15} strokeWidth={1.2} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </Card>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card title="Monthly returns" className="xl:col-span-2"><MonthGrid m={s.monthly} /></Card>
        <Card title="All strategies, same start">
          <div className="h-[230px]">
            <ResponsiveContainer>
              <LineChart data={data.curve} margin={{ left: 4, right: 4 }}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="date" tickLine={false} axisLine={false} minTickGap={80} />
                <YAxis scale="log" domain={["auto", "auto"]} tickFormatter={(v) => compact(v, "$")} tickLine={false} axisLine={false} width={52} />
                <Tooltip {...chartTooltip} formatter={(v: number, n: string) => [usd(v), n]} />
                <Line dataKey="swing" stroke="var(--color-up)" dot={false} strokeWidth={1.6} name="Swing 5d" />
                <Line dataKey="stock" stroke="var(--color-sky)" dot={false} strokeWidth={1.6} name="Day L/S" />
                <Line dataKey="options" stroke="var(--color-accent)" dot={false} strokeWidth={1.6} name="Options" />
                <Line dataKey="straddle" stroke="var(--color-amber)" dot={false} strokeWidth={1.6} name="Straddles" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      <details className="mt-6">
        <summary className="card flex items-center justify-between px-5 py-3.5 text-[15px] font-semibold text-ink-100 hover:border-ink-600">
          Evidence and method: signal quality, model comparison, trade log, assumptions <span className="text-[13px] font-normal text-ink-400">show</span>
        </summary>
      <h2 className="mb-3 mt-6 text-[16px] font-semibold text-ink-100">Is the signal real?</h2>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
        <Stat label="Information coeff." value={num(sig.ic.ic_mean, 3)} sub="daily rank corr. with open→close" hint="0.02-0.05 is a strong daily equity signal" />
        <Stat label="IC t-stat (ann.)" value={num(sig.ic.ic_ir, 1)} sub="IC mean / std × √252" />
        <Stat label="Hit rate (all)" value={pct(sig.ic.hit_rate_all, 1, false)} sub="direction, liquid universe" />
        <Stat label="AUC" value={num(sig.ic.auc, 3)} sub="0.5 = coin flip" />
        <Stat label="Calibration slope" value={num(data.model.cal_slope, 2)} sub="raw → honest shrink" />
      </div>
      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card title="Return by signal decile" right={<span className="text-[12.5px] text-ink-500">avg open→close, bps</span>}>
          <div className="h-[230px]">
            <ResponsiveContainer>
              <BarChart data={sig.deciles} margin={{ left: -8 }}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="decile" tickLine={false} axisLine={false} />
                <YAxis tickLine={false} axisLine={false} />
                <Tooltip {...chartTooltip} formatter={(v: number) => [`${v.toFixed(1)} bps`, "avg return"]} labelFormatter={(l) => `Decile ${l}`} />
                <ReferenceLine y={0} stroke="var(--color-ink-500)" />
                <Bar dataKey="ret_bps" radius={[4, 4, 0, 0]}>{sig.deciles.map((d) => <Cell key={d.decile} fill={d.ret_bps >= 0 ? "var(--color-up)" : "var(--color-down)"} fillOpacity={0.35 + Math.abs(d.decile - 5.5) / 8} />)}</Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Calibration" right={<span className="text-[12.5px] text-ink-500">predicted vs realised P(up)</span>}>
          <div className="h-[230px]">
            <ResponsiveContainer>
              <ComposedChart data={sig.calibration} margin={{ left: -8 }}>
                <CartesianGrid />
                <XAxis dataKey="pred" type="number" domain={["dataMin", "dataMax"]} tickFormatter={(v) => pct(v, 0, false)} tickLine={false} axisLine={false} />
                <YAxis domain={[0.4, 0.6]} tickFormatter={(v) => pct(v, 0, false)} tickLine={false} axisLine={false} />
                <Tooltip {...chartTooltip} formatter={(v: number, n: string) => [n === "n" ? v : pct(v, 1, false), n]} />
                <Line dataKey="pred" stroke="var(--color-ink-500)" strokeDasharray="4 4" dot={false} name="perfect" />
                <Scatter dataKey="actual" fill="var(--color-accent)" name="actual" />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Feature importance" right={<span className="text-[12.5px] text-ink-500">nonlinear model, gain share</span>}>
          <div className="space-y-1.5">
            {data.model.importances.slice(0, 11).map((f) => (
              <div key={f.feature} className="grid grid-cols-[1fr_110px_44px] items-center gap-2 text-[13px]">
                <span className="num truncate text-ink-300">{f.feature}</span>
                <div className="h-1.5 rounded-full bg-ink-800"><div className="h-full rounded-full bg-gradient-to-r from-accent to-sky" style={{ width: `${(f.importance / data.model.importances[0].importance) * 100}%` }} /></div>
                <span className="num text-right text-ink-400">{pct(f.importance, 1, false)}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {research && <ResearchCard r={research} />}
      <AblationCard />

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card title="Per-ticker model skill" right={<span className="text-[12.5px] text-ink-500">out-of-sample IC by ticker</span>}>
          <div className="h-[150px]">
            <ResponsiveContainer>
              <BarChart data={data.ticker_ic.dist.map((c, i) => ({ c, x: (data.ticker_ic.edges[i] + data.ticker_ic.edges[i + 1]) / 2 }))} margin={{ left: -20 }}>
                <XAxis dataKey="x" tickFormatter={(v) => v.toFixed(2)} tickLine={false} axisLine={false} />
                <YAxis tickLine={false} axisLine={false} />
                <Tooltip {...chartTooltip} formatter={(v: number) => [v, "tickers"]} labelFormatter={(l) => `IC ≈ ${Number(l).toFixed(3)}`} />
                <ReferenceLine x={0} stroke="var(--color-ink-400)" />
                <Bar dataKey="c">{data.ticker_ic.dist.map((_, i) => <Cell key={i} fill={data.ticker_ic.edges[i] >= 0 ? "var(--color-up)" : "var(--color-down)"} fillOpacity={0.6} />)}</Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-3 text-[13px]">
            <div><div className="label mb-1">Most predictable</div>{data.ticker_ic.best.filter((t) => t.ic != null).slice(0, 5).map((t) => <Link key={t.ticker} to={`/t/${t.ticker}`} className="flex justify-between py-0.5 hover:text-accent"><span>{t.ticker}</span><span className="num text-up">{t.ic.toFixed(3)}</span></Link>)}</div>
            <div><div className="label mb-1">Least predictable</div>{data.ticker_ic.worst.filter((t) => t.ic != null).slice(0, 5).map((t) => <Link key={t.ticker} to={`/t/${t.ticker}`} className="flex justify-between py-0.5 hover:text-accent"><span>{t.ticker}</span><span className="num text-down">{t.ic.toFixed(3)}</span></Link>)}</div>
          </div>
        </Card>
        <Card title="Recent trades" className="xl:col-span-2" pad={false}>
          <div className="max-h-[330px] overflow-auto px-5 pb-4">
            <table className="w-full text-[13px]">
              <thead className="sticky top-0 bg-ink-900"><tr className="text-left text-ink-500">
                <th className="py-2 font-medium">Date</th><th className="font-medium">Ticker</th><th className="font-medium">Side</th>
                <th className="font-medium">Conf</th><th className="font-medium">Model EV</th><th className="font-medium">Stock move</th>
                <th className="font-medium text-right">Return</th><th className="font-medium text-right">P&L</th></tr></thead>
              <tbody>{trades.map((t, i) => (
                <tr key={i} className="border-t border-ink-800/80">
                  <td className="num py-1.5 text-ink-400">{t.date}</td>
                  <td><Link to={`/t/${t.ticker}`} className="font-semibold hover:text-accent">{t.ticker}</Link>{t.earnings ? <span className="ml-1 text-amber">●</span> : null}</td>
                  <td><Pill color={["CALL", "LONG"].includes(t.side) ? "up" : t.side === "STRADDLE" ? "amber" : "down"}>{t.side}</Pill></td>
                  <td className="num">{t.conf ? pct(t.conf, 1, false) : "–"}</td>
                  <td className="num">{k === "earnings_straddle" ? `${num(t.ev + 1, 2)}×` : pct(t.ev, k === "stock_daytrade" ? 2 : 1)}</td>
                  <td className={clsx("num", tone(t.stock_move))}>{pct(t.stock_move, 2)}</td>
                  <td className={clsx("num text-right", tone(t.ret))}>{pct(t.ret, 1)}</td>
                  <td className={clsx("num text-right", tone(t.pnl))}>{usd(t.pnl)}</td>
                </tr>))}</tbody>
            </table>
          </div>
        </Card>
      </div>

      <Card className="mt-4" title="Assumptions">
        <div className="grid grid-cols-2 gap-4 text-[13px] text-ink-300 md:grid-cols-4">
          <div><div className="label mb-1">Entry / exit</div>Signals use only data published before 09:00 ET. Fills at the official open and close.</div>
          <div><div className="label mb-1">Options pricing</div>Black-Scholes on front-week IV. Open IV marked up with the size of the premarket gap. One session of theta. Post-earnings IV crush.</div>
          <div><div className="label mb-1">Costs</div>Cross half the bid-ask each side, ${data.costs.option_commission_per_contract}/contract, {data.costs.stock_slippage_bps} bps stock slippage per side.</div>
          <div><div className="label mb-1">Risk</div>¼ Kelly, ≤{pct(data.risk.max_risk_per_trade, 0, false)} premium per trade, ≤{pct(data.risk.max_daily_risk, 0, false)} per day, size halved after a −{pct(data.risk.daily_loss_stop, 0, false)} day.</div>
        </div>
      </Card>
      </details>
    </div>
  );
}

function MonthGrid({ m }: { m: { year: number; month: number; ret: number }[] }) {
  const years = [...new Set(m.map((x) => x.year))];
  const get = (y: number, mo: number) => m.find((x) => x.year === y && x.month === mo)?.ret;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12.5px]">
        <thead><tr className="text-ink-500"><th className="w-12 text-left font-medium" />{MONTHS.map((x) => <th key={x} className="font-medium">{x}</th>)}<th className="font-medium">Year</th></tr></thead>
        <tbody>{years.map((y) => {
          const yr = m.filter((x) => x.year === y).reduce((a, b) => a * (1 + b.ret), 1) - 1;
          return (
            <tr key={y}>
              <td className="num py-1 text-ink-400">{y}</td>
              {MONTHS.map((_, i) => {
                const v = get(y, i + 1);
                const a = v == null ? 0 : Math.min(1, Math.abs(v) / 0.12);
                return <td key={i} className="p-0.5"><div className="num rounded-md py-1.5 text-center"
                  style={{ background: v == null ? "transparent" : v >= 0 ? `color-mix(in srgb, var(--color-up) ${Math.round((0.08 + a * 0.5) * 100)}%, transparent)` : `color-mix(in srgb, var(--color-down) ${Math.round((0.08 + a * 0.5) * 100)}%, transparent)`, color: v == null ? "var(--color-ink-600)" : "var(--color-ink-100)" }}>
                  {v == null ? "·" : pct(v, 1)}</div></td>;
              })}
              <td className={clsx("num px-2 text-right font-semibold", tone(yr))}>{pct(yr, 0)}</td>
            </tr>);
        })}</tbody>
      </table>
    </div>
  );
}

function AblationCard() {
  const { data } = useApi<Ablation>("ablation");
  if (!data) return null;
  const rows = data.results.filter((r) => r.ic != null);
  const best = Math.max(...rows.map((r) => r.spread_bps ?? 0));
  const mm = data.market_model;
  return (
    <Card className="mt-4" title="What actually predicts next-session direction"
      right={<span className="text-[12.5px] text-ink-500">holdout from {data.split}; groups added one at a time</span>}>
      <div className="grid gap-5 xl:grid-cols-[1fr_320px]">
        <table className="w-full text-[13.5px]">
          <thead><tr className="text-left text-ink-500">
            <th className="py-1.5 font-medium">Target</th><th className="font-medium">Feature group added</th>
            <th className="font-medium text-right">IC</th><th className="font-medium text-right">t-stat</th>
            <th className="font-medium text-right">Top-bottom decile</th><th className="font-medium text-right">Top decile up</th>
            <th className="pl-4 font-medium">Strongest new features</th></tr></thead>
          <tbody>{data.results.map((r, i) => (
            <tr key={i} className="border-t border-ink-800/70">
              <td className="py-1.5 text-ink-400">{r.target === "z_x" ? "vs market" : "raw"}</td>
              <td className="text-ink-200">{r.step.replace("+ ", "")}</td>
              {r.ic == null ? <td colSpan={5} className="text-ink-500">{r.note}</td> : (<>
                <td className={clsx("num text-right", tone(r.ic))}>{num(r.ic, 4)}</td>
                <td className="num text-right">{num(r.ic_t, 1)}</td>
                <td className={clsx("num text-right", r.spread_bps === best && "font-semibold text-accent")}>{num(r.spread_bps, 1)} bps</td>
                <td className="num text-right">{pct(r.hit_top, 1, false)}</td>
                <td className="num truncate pl-4 text-[12.5px] text-ink-500">{(r.top_new ?? []).join(", ")}</td></>)}
            </tr>))}</tbody>
        </table>
        {mm && (
          <div className="rounded-xl border border-ink-800 bg-ink-950/40 p-4 text-[13.5px]">
            <div className="label mb-2">Market direction from the political world</div>
            <div className="num text-[22px] font-semibold">{pct(mm.gbm.hit, 1, false)}</div>
            <div className="text-ink-400">days called right, vs {pct(1 - mm.gbm.base_up_rate, 1, false)} for "always down" and {pct(mm.gbm.base_up_rate, 1, false)} for "always up", over {mm.gbm.days} sessions. Sharpe {num(mm.gbm.sharpe, 2)}.</div>
            <div className="mt-2 text-ink-500">Policy uncertainty, geopolitical risk, rates, credit and volatility did not forecast the next session's market direction better than chance, so they are shown as context, not used as stock signals.</div>
          </div>)}
      </div>
    </Card>
  );
}

function ResearchCard({ r }: { r: Research }) {
  const best = Math.max(...r.variants.map((v) => v.sharpe));
  return (
    <Card className="mt-4" title="Research holdout: which model design holds up"
      right={<span className="text-[12.5px] text-ink-500">trained before {r.split} with a {r.purge_sessions}-session purge · {r.book} · {r.cost_bps_round_trip} bps round trip · see docs/RESEARCH.md</span>}>
      <table className="w-full text-[13.5px]">
        <thead><tr className="text-left text-ink-500">
          <th className="py-1.5 font-medium">Variant</th><th className="font-medium text-right">Hold</th><th className="font-medium text-right">IC</th>
          <th className="font-medium text-right" title="t > 3 is the bar suggested by Harvey, Liu and Zhu (2016)">t-stat</th>
          <th className="font-medium text-right">Net bps/day</th><th className="font-medium text-right">Net / yr</th><th className="font-medium text-right">Sharpe</th>
          <th className="font-medium text-right" title="share of longs that beat the market / shorts that lagged it">Hit long / short</th>
          <th className="pl-4 font-medium">Net bps/day by year</th></tr></thead>
        <tbody>{r.variants.map((v) => (
          <tr key={v.variant} className="border-t border-ink-800/70" title={v.note}>
            <td className="py-1.5 text-ink-200">{v.variant}</td>
            <td className="num text-right text-ink-400">{v.hold_days}d</td>
            <td className="num text-right">{num(v.ic, 4)}</td>
            <td className={clsx("num text-right", v.ic_t >= 3 ? "text-up" : "text-ink-300")}>{num(v.ic_t, 1)}</td>
            <td className={clsx("num text-right", tone(v.net_bps_day))}>{num(v.net_bps_day, 1)}</td>
            <td className={clsx("num text-right", tone(v.net_ann))}>{pct(v.net_ann, 0)}</td>
            <td className={clsx("num text-right", v.sharpe === best && "font-semibold text-accent")}>{num(v.sharpe, 2)}</td>
            <td className="num text-right">{pct(v.hit_long, 1, false)} / {pct(v.hit_short, 1, false)}</td>
            <td className="num pl-4 text-[12.5px]">{Object.entries(v.by_year).map(([y, b]) => <span key={y} className={clsx("mr-3", tone(b))}>{y}: {num(b, 1)}</span>)}</td>
          </tr>))}</tbody>
      </table>
    </Card>
  );
}
