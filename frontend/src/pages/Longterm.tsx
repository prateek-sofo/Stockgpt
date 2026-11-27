import clsx from "clsx";
import { Link } from "react-router-dom";
import { Area, AreaChart, CartesianGrid, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, chartTooltip, ErrorBox, Loading, PageHeader, Stat } from "../components/ui";
import { useApi } from "../lib/api";
import { num, pct, tone } from "../lib/format";
import type { Longterm as LT } from "../lib/types";

export default function Longterm() {
  const { data, error } = useApi<LT>("longterm");
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const s = data.backtest.stats;
  return (
    <div>
      <PageHeader title="Long-term ranking" sub="Monthly composite of fundamentals (growth, margins, cash flow, leverage, valuation), 3-month momentum and the 60-day news-sentiment trend. Top quintile, rebalanced every 21 sessions." />
      <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
        <Stat label="Top quintile CAGR" value={pct(s.cagr_top, 1)} valueClass={tone(s.cagr_top)} />
        <Stat label="Universe CAGR" value={pct(s.cagr_universe, 1)} />
        <Stat label="Excess / yr" value={pct(s.excess_annual, 1)} valueClass={tone(s.excess_annual)} />
        <Stat label="Information ratio" value={num(s.info_ratio, 2)} />
        <Stat label="Months beating" value={pct(s.hit_rate, 0, false)} sub={`${s.periods} rebalances`} />
      </div>
      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[1fr_440px]">
        <Card title="Growth of $1: top quintile vs equal-weight universe">
          <div className="h-[360px]">
            <ResponsiveContainer>
              <AreaChart data={data.backtest.curve} margin={{ left: 0, right: 8, top: 8 }}>
                <defs><linearGradient id="lt" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="var(--color-up)" stopOpacity={0.3} /><stop offset="1" stopColor="var(--color-up)" stopOpacity={0} /></linearGradient></defs>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="date" tickLine={false} axisLine={false} minTickGap={60} />
                <YAxis tickFormatter={(v) => `$${v.toFixed(2)}`} tickLine={false} axisLine={false} width={50} domain={["auto", "auto"]} />
                <Tooltip {...chartTooltip} formatter={(v: number, n: string) => [`$${v.toFixed(3)}`, n]} />
                <Area dataKey="top" stroke="var(--color-up)" fill="url(#lt)" strokeWidth={2} name="Top quintile" />
                <Line dataKey="universe" stroke="var(--color-ink-400)" strokeDasharray="4 4" dot={false} name="Universe" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Current top picks" pad={false}>
          <div className="max-h-[400px] overflow-auto px-5 pb-4">
            {data.ranking.map((r, i) => (
              <Link key={r.ticker} to={`/t/${r.ticker}`} className="flex items-start gap-3 border-t border-ink-800/70 py-2.5 first:border-0 hover:bg-ink-800/30">
                <span className="num w-6 pt-0.5 text-[12.5px] text-ink-500">{i + 1}</span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2"><span className="font-semibold">{r.ticker}</span><span className="truncate text-[12.5px] text-ink-500">{r.name}</span></div>
                  <div className="mt-1 flex flex-wrap gap-1">{r.drivers.map((d) => (
                    <span key={d.label} className={clsx("chip ring-1", d.z > 0 ? "bg-up/8 text-up ring-up/20" : "bg-down/8 text-down ring-down/20")}>{d.label} {d.z > 0 ? "+" : ""}{d.z.toFixed(1)}σ</span>))}</div>
                </div>
                <span className="num text-[13px] text-accent">{r.score.toFixed(2)}</span>
              </Link>))}
          </div>
        </Card>
      </div>
    </div>
  );
}
