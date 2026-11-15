import clsx from "clsx";
import { useMemo, useState } from "react";
import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, chartTooltip, ErrorBox, Loading, PageHeader, Stat, Tabs } from "../components/ui";
import { useApi } from "../lib/api";
import { compact, num, pct, usd } from "../lib/format";
import type { Backtest } from "../lib/types";

function rng(seed: number) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

function simulate(daily: number[], start: number, target: number, days: number, scale: number, sims = 3000) {
  const r = rng(42 + Math.round(scale * 100));
  const ends: number[] = [];
  const at: number[][] = Array.from({ length: days + 1 }, () => []);
  let hit = 0, ruin = 0, dd50 = 0;
  for (let i = 0; i < sims; i++) {
    let v = start, peak = start, mdd = 0, reached = false;
    at[0].push(v);
    for (let d = 1; d <= days; d++) {
      v *= Math.max(0, 1 + daily[Math.floor(r() * daily.length)] * scale);
      peak = Math.max(peak, v);
      mdd = Math.min(mdd, v / peak - 1);
      if (v >= target) reached = true;
      at[d].push(v);
    }
    ends.push(v);
    if (reached) hit++;
    if (v < start * 0.2) ruin++;
    if (mdd <= -0.5) dd50++;
  }
  const q = (a: number[], p: number) => { const s = [...a].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(p * s.length))]; };
  const step = Math.max(1, Math.floor(days / 80));
  const fan = [];
  for (let d = 0; d <= days; d += step) fan.push({ day: d, band: [q(at[d], 0.05), q(at[d], 0.95)], iqr: [q(at[d], 0.25), q(at[d], 0.75)], p50: q(at[d], 0.5) });
  return { pTarget: hit / sims, pRuin: ruin / sims, pDD: dd50 / sims, pLoss: ends.filter((e) => e < start).length / sims,
    median: q(ends, 0.5), p5: q(ends, 0.05), p95: q(ends, 0.95), fan };
}

export default function Planner() {
  const { data, error } = useApi<Backtest>("backtest");
  const [start, setStart] = useState(25000);
  const [target, setTarget] = useState(10_000_000);
  const [days, setDays] = useState(252);
  const [scale, setScale] = useState(1);
  const [strat, setStrat] = useState<"stock" | "options" | "straddle">("stock");
  const daily = useMemo(() => {
    if (!data) return [];
    const c = data.curve.map((r) => r[strat]);
    return c.slice(1).map((v, i) => v / c[i] - 1);
  }, [data, strat]);
  const sim = useMemo(() => (daily.length ? simulate(daily, start, target, days, scale) : null), [daily, start, target, days, scale]);
  if (error) return <ErrorBox error={error} />;
  if (!data || !sim) return <Loading />;
  const req = Math.pow(target / start, 1 / days) - 1;
  const mean = daily.reduce((a, b) => a + b, 0) / daily.length;
  const geo = Math.exp(daily.reduce((a, b) => a + Math.log(1 + b), 0) / daily.length) - 1;
  const cagr = Math.pow(1 + geo * scale, 252) - 1;
  const yearsNeeded = geo > 0 ? Math.log(target / start) / Math.log(1 + cagr) : Infinity;
  const multiple = target / start;
  return (
    <div>
      <PageHeader title="Growth Planner" sub="What it takes to reach a target, measured against this system's own out-of-sample daily returns (bootstrapped)." />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[360px_1fr]">
        <Card title="Goal">
          <div className="space-y-5">
            <Field label="Starting capital" value={usd(start)}><input type="range" min={5000} max={500000} step={5000} value={start} onChange={(e) => setStart(+e.target.value)} className="w-full accent-[#8b7bff]" /></Field>
            <Field label="Target" value={usd(target)}>
              <div className="flex flex-wrap gap-1.5">{[100_000, 250_000, 1_000_000, 10_000_000].map((t) => (
                <button key={t} onClick={() => setTarget(t)} className={clsx("rounded-lg border px-2.5 py-1 text-[12px]", t === target ? "border-accent bg-accent/15 text-ink-100" : "border-ink-700 text-ink-400 hover:text-ink-200")}>{compact(t, "$")}</button>))}</div>
            </Field>
            <Field label="Horizon" value={`${days} trading days (${(days / 252).toFixed(1)} yr)`}><input type="range" min={63} max={1260} step={21} value={days} onChange={(e) => setDays(+e.target.value)} className="w-full accent-[#8b7bff]" /></Field>
            <Field label="Strategy returns"><Tabs value={strat} onChange={setStrat} items={[{ v: "stock", label: "Shares" }, { v: "options", label: "Options" }, { v: "straddle", label: "Straddles" }]} /></Field>
            <Field label="Position size multiplier" value={`${scale.toFixed(1)}×`}>
              <input type="range" min={0.25} max={6} step={0.25} value={scale} onChange={(e) => setScale(+e.target.value)} className="w-full accent-[#8b7bff]" />
              <div className="mt-1 text-[11px] text-ink-500">1× = backtest sizing. Higher = more leverage on the same edge.</div>
            </Field>
          </div>
        </Card>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat label="Required per day" value={pct(req, 2)} sub={`${num(multiple, 0)}× in ${days} sessions, compounded`} valueClass="text-amber" />
            <Stat label="Strategy per day" value={pct(geo * scale, 3)} sub={`geometric · arithmetic ${pct(mean * scale, 3)}`} valueClass={geo > 0 ? "text-up" : "text-down"} />
            <Stat label="P(reach target)" value={pct(sim.pTarget, 1, false)} sub="at any point in the horizon" valueClass={sim.pTarget > 0.5 ? "text-up" : "text-ink-100"} />
            <Stat label="Years at this edge" value={isFinite(yearsNeeded) ? num(yearsNeeded, 1) : "∞"} sub={`at ${pct(cagr, 0)} CAGR (${scale}× size)`} />
          </div>
          <Card title="1-year outcome distribution" right={<span className="text-[11px] text-ink-500">3,000 bootstrapped paths · 5-95% and 25-75% bands</span>}>
            <div className="h-[340px]">
              <ResponsiveContainer>
                <ComposedChart data={sim.fan} margin={{ left: 8, right: 8, top: 8 }}>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="day" tickLine={false} axisLine={false} tickFormatter={(d) => `d${d}`} />
                  <YAxis scale="log" domain={["auto", "auto"]} tickFormatter={(v) => compact(v, "$")} tickLine={false} axisLine={false} width={60} allowDataOverflow />
                  <Tooltip {...chartTooltip} formatter={(v: number | number[], n: string) => [Array.isArray(v) ? `${usd(v[0])} – ${usd(v[1])}` : usd(v), n]} labelFormatter={(d) => `Day ${d}`} />
                  <Area dataKey="band" stroke="none" fill="#8b7bff" fillOpacity={0.12} name="5-95%" />
                  <Area dataKey="iqr" stroke="none" fill="#8b7bff" fillOpacity={0.25} name="25-75%" />
                  <Line dataKey="p50" stroke="#8b7bff" strokeWidth={2} dot={false} name="median" />
                  <ReferenceLine y={target} stroke="#ffb547" strokeDasharray="5 5" label={{ value: `target ${compact(target, "$")}`, fill: "#ffb547", fontSize: 11, position: "insideTopLeft" }} />
                  <ReferenceLine y={start} stroke="#3a4560" strokeDasharray="3 3" />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </Card>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
            <Stat label="Median end" value={compact(sim.median, "$")} />
            <Stat label="5th pct end" value={compact(sim.p5, "$")} valueClass="text-down" />
            <Stat label="95th pct end" value={compact(sim.p95, "$")} valueClass="text-up" />
            <Stat label="P(lose money)" value={pct(sim.pLoss, 1, false)} />
            <Stat label="P(−50% drawdown)" value={pct(sim.pDD, 1, false)} sub={`P(ruin, −80%): ${pct(sim.pRuin, 1, false)}`} />
          </div>
          <Card title="The arithmetic">
            <div className="grid gap-4 text-[13px] leading-relaxed text-ink-300 md:grid-cols-3">
              <p><b className="text-ink-100">{compact(start, "$")} → {compact(target, "$")}</b> is a {num(multiple, 0)}× multiple. Over {days} sessions that needs <b className="num text-amber">{pct(req, 2)}</b> compounded every trading day, with no losing streak large enough to break the chain.</p>
              <p>Volatility drag: a strategy's growth rate is roughly <span className="num">mean − variance/2</span>. Sizing up raises the mean linearly and the drag quadratically, so past the Kelly point more leverage <i>lowers</i> long-run growth and raises the odds of ruin. Push the multiplier and watch the median and ruin probability move.</p>
              <p>Reference points: the S&amp;P 500 averages ~10%/yr. The best-known quant fund's widely reported gross average is ~66%/yr. The planner shows the outcome distribution this system's measured edge supports. Rerun it on your real data once the pipeline ingests it.</p>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Field({ label, value, children }: { label: string; value?: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between"><span className="label">{label}</span>{value && <span className="num text-[13px] text-ink-100">{value}</span>}</div>
      {children}
    </div>
  );
}
