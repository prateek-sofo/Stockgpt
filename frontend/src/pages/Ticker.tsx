import clsx from "clsx";
import { ArrowLeft, Brain, CalendarClock, Newspaper } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import CandleChart from "../components/CandleChart";
import { Card, chartTooltip, ErrorBox, Loading, PageHeader, Pill, SentDot, Stat, Tabs } from "../components/ui";
import { useApi } from "../lib/api";
import { compact, num, pct, tone, TRAIT_LABEL, usd } from "../lib/format";
import type { TickerProfile } from "../lib/types";

export default function Ticker() {
  const { sym = "" } = useParams();
  const { data, error } = useApi<TickerProfile>(`ticker/${sym.toUpperCase()}`);
  const [range, setRange] = useState<"60" | "120" | "260">("120");
  const [newsFilter, setNewsFilter] = useState<"all" | "material">("material");
  if (error) return <ErrorBox error={`${sym.toUpperCase()}: ${error}`} />;
  if (!data) return <Loading />;
  const px = data.prices.slice(-Number(range));
  const last = data.prices[data.prices.length - 1];
  const first = px[0];
  const perf = data.performance;
  const traitRows = Object.entries(data.model);
  const news = data.news.filter((n) => newsFilter === "all" || n.m >= 0.4);
  return (
    <div>
      <Link to="/universe" className="mb-3 inline-flex items-center gap-1 text-[12px] text-ink-400 hover:text-ink-200"><ArrowLeft className="h-3.5 w-3.5" />Universe</Link>
      <PageHeader
        title={<span className="flex items-center gap-3">{data.ticker}<span className="text-[15px] font-normal text-ink-400">{data.name}</span>
          {data.liquid ? <Pill color="up">liquid</Pill> : <Pill>illiquid</Pill>}
          {data.stats.days_to_earn <= 10 && <Pill color="amber"><CalendarClock className="h-3 w-3" />earnings in {data.stats.days_to_earn}d</Pill>}</span>}
        sub={`${data.sector} · market cap ${compact(data.market_cap, "$")}`}
        right={<div className="text-right"><div className="num text-[28px] font-semibold">{usd(last.c, 2)}</div>
          <div className={clsx("num text-[13px]", tone(last.c / first.c - 1))}>{pct(last.c / first.c - 1, 1)} over {range}d</div></div>} />

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4 xl:grid-cols-8">
        <Stat label="Realised vol" value={pct(data.stats.rv20, 0, false)} />
        <Stat label="IV30" value={pct(data.stats.iv30, 0, false)} sub={`IV rank ${pct(data.stats.iv_rank, 0, false)}`} />
        <Stat label="$ volume" value={compact(data.stats.adv, "$")} />
        <Stat label="Options / day" value={compact(data.stats.opt_vol)} sub={`spread ${pct(data.stats.spread, 1, false)}`} />
        <Stat label="Avg earnings move" value={pct(data.stats.hist_earn_move, 1, false)} />
        <Stat label="Model IC" value={num(perf.ic, 3)} valueClass={tone(perf.ic)} sub={`${perf.days ?? 0} OOS days`} />
        <Stat label="Hit rate (conf.)" value={pct(perf.hit_conf ?? null, 1, false)} sub={`${perf.n_conf ?? 0} confident calls`} />
        <Stat label="Signal return" value={`${num(perf.signal_ret_bps, 1)}`} sub="bps/day following model sign" valueClass={tone(perf.signal_ret_bps)} />
      </div>

      <Card className="mt-4" title="Price, overnight news, earnings" right={<Tabs value={range} onChange={setRange} items={[{ v: "60", label: "3M" }, { v: "120", label: "6M" }, { v: "260", label: "1Y" }]} />}>
        <CandleChart data={px} />
      </Card>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title={<span className="flex items-center gap-2"><Brain className="h-4 w-4 text-accent" />{data.ticker}'s own model</span>}
          right={<span className="text-[11px] text-ink-500">effective coefficient = market + this ticker's shrunk trait</span>}>
          <div className="grid gap-3 md:grid-cols-2">
            {traitRows.map(([k, m]) => {
              const L = TRAIT_LABEL[k];
              const mkt = m.market_beta;
              const max = Math.max(Math.abs(m.effective), Math.abs(mkt), 1e-6) * 1.3;
              const conf = Math.min(1, m.evidence / (m.evidence + (k === "news_on_wsent" ? 30 : k === "eps_surprise" ? 0.5 : 150)));
              return (
                <div key={k} className="rounded-xl border border-ink-800 bg-ink-950/40 p-4">
                  <div className="flex items-center justify-between">
                    <span className="text-[13px] font-semibold">{L?.name ?? k}</span>
                    <span className="num text-[11px] text-ink-500">{pct(conf, 0, false)} own data</span>
                  </div>
                  <div className="mt-1 text-[12px] text-ink-400">{data.ticker} {m.effective >= 0 ? L?.pos : L?.neg}{Math.abs(m.effective) > Math.abs(mkt) * 1.25 ? ", more than the market" : Math.abs(m.effective) < Math.abs(mkt) * 0.75 ? ", less than the market" : ", in line with the market"}.</div>
                  <div className="mt-3 space-y-2 text-[11px]">
                    {[{ l: "Market", v: mkt, c: "#5d6a88" }, { l: data.ticker, v: m.effective, c: m.effective >= 0 ? "#2ee6a6" : "#ff5c7a" }].map((b) => (
                      <div key={b.l} className="grid grid-cols-[56px_1fr_56px] items-center gap-2">
                        <span className="text-ink-400">{b.l}</span>
                        <div className="relative h-2 rounded-full bg-ink-800"><div className="absolute inset-y-0 w-px bg-ink-500" style={{ left: "50%" }} />
                          <div className="absolute inset-y-0 rounded-full" style={{ background: b.c, left: b.v >= 0 ? "50%" : `${50 - (Math.abs(b.v) / max) * 50}%`, width: `${(Math.abs(b.v) / max) * 50}%` }} /></div>
                        <span className="num text-right text-ink-200">{b.v >= 0 ? "+" : ""}{b.v.toFixed(3)}</span>
                      </div>))}
                  </div>
                </div>);
            })}
          </div>
          <div className="mt-4 h-[180px]">
            <div className="label mb-1">How its behaviour estimates evolved (point-in-time)</div>
            <ResponsiveContainer>
              <LineChart data={data.traits} margin={{ left: -10, right: 8 }}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="d" tickLine={false} axisLine={false} minTickGap={70} />
                <YAxis tickLine={false} axisLine={false} />
                <Tooltip {...chartTooltip} formatter={(v: number, n: string) => [v.toFixed(3), TRAIT_LABEL[n]?.name ?? n]} />
                <Legend formatter={(v) => <span className="text-[11px] text-ink-400">{TRAIT_LABEL[v]?.name ?? v}</span>} />
                <ReferenceLine y={0} stroke="#3a4560" />
                <Line dataKey="news_on_wsent" stroke="#8b7bff" dot={false} strokeWidth={1.8} />
                <Line dataKey="gap_pre_z" stroke="#4cc3ff" dot={false} strokeWidth={1.4} />
                <Line dataKey="ret_1d_z" stroke="#ffb547" dot={false} strokeWidth={1.4} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card title={<span className="flex items-center gap-2"><CalendarClock className="h-4 w-4 text-amber" />Earnings: implied vs actual</span>}>
          {data.earnings.length === 0 ? <div className="py-8 text-center text-[12px] text-ink-500">No earnings history yet.</div> : (<>
            <div className="h-[210px]">
              <ResponsiveContainer>
                <BarChart data={data.earnings.slice(-12)} margin={{ left: -14 }}>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="d" tickFormatter={(d) => d.slice(2, 7)} tickLine={false} axisLine={false} />
                  <YAxis tickFormatter={(v) => pct(v, 0, false)} tickLine={false} axisLine={false} />
                  <Tooltip {...chartTooltip} formatter={(v: number, n: string) => [pct(v, 1, false), n]} />
                  <Bar dataKey="implied" fill="#3a4560" radius={[3, 3, 0, 0]} name="implied" />
                  <Bar dataKey="actual" radius={[3, 3, 0, 0]} name="actual">{data.earnings.slice(-12).map((e, i) => <Cell key={i} fill={e.signed >= 0 ? "#2ee6a6" : "#ff5c7a"} />)}</Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            {(() => {
              const beat = data.earnings.filter((e) => e.actual > e.implied).length;
              const avgI = data.earnings.reduce((a, e) => a + e.implied, 0) / data.earnings.length;
              const avgA = data.earnings.reduce((a, e) => a + e.actual, 0) / data.earnings.length;
              return <div className="mt-3 grid grid-cols-3 gap-2 text-center text-[11px]">
                <div><div className="text-ink-500">Avg implied</div><div className="num text-[14px]">{pct(avgI, 1, false)}</div></div>
                <div><div className="text-ink-500">Avg actual</div><div className="num text-[14px]">{pct(avgA, 1, false)}</div></div>
                <div><div className="text-ink-500">Beat implied</div><div className={clsx("num text-[14px]", beat / data.earnings.length > 0.5 ? "text-up" : "text-ink-200")}>{beat}/{data.earnings.length}</div></div>
              </div>;
            })()}
          </>)}
        </Card>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title={<span className="flex items-center gap-2"><Newspaper className="h-4 w-4 text-sky" />News feed, scored</span>}
          right={<Tabs value={newsFilter} onChange={setNewsFilter} items={[{ v: "material", label: "Material" }, { v: "all", label: "All" }]} />} pad={false}>
          <div className="max-h-[420px] overflow-auto px-5 pb-4">
            {news.map((n, i) => (
              <div key={i} className="flex items-start gap-3 border-t border-ink-800/70 py-2.5 first:border-0">
                <SentDot s={n.s} />
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] leading-snug text-ink-100">{n.h}</div>
                  <div className="mt-0.5 flex flex-wrap gap-x-3 text-[11px] text-ink-500">
                    <span className="num">{n.t} ET</span><span>{n.src}</span><span>{n.e}</span>
                    <span>{n.on ? `overnight → ${n.d} open` : `intraday, priced by ${n.d}`}</span>
                  </div>
                </div>
                <span className={clsx("num shrink-0 text-[12px]", tone(n.s))}>{n.s > 0 ? "+" : ""}{n.s.toFixed(2)}</span>
              </div>))}
          </div>
        </Card>
        <Card title="Trading record on this ticker" pad={false}>
          <div className="px-5 pb-4">
            <div className="mb-3 grid grid-cols-3 gap-2 text-[11px]">
              {Object.entries(perf.trades ?? {}).map(([k, v]) => (
                <div key={k} className="rounded-lg border border-ink-800 p-2">
                  <div className="truncate text-ink-500">{k.replace("_", " ")}</div>
                  <div className="num text-[13px]">{v.n} · {pct(v.win, 0, false)}</div>
                  <div className={clsx("num", tone(v.pnl))}>{usd(v.pnl)}</div>
                </div>))}
            </div>
            <div className="max-h-[300px] overflow-auto">
              <table className="w-full text-[11.5px]">
                <tbody>{(perf.recent_trades ?? []).slice().reverse().map((t, i) => (
                  <tr key={i} className="border-t border-ink-800/70">
                    <td className="num py-1.5 text-ink-400">{t.date}</td>
                    <td><Pill color={["CALL", "LONG"].includes(t.side) ? "up" : t.side === "STRADDLE" ? "amber" : "down"}>{t.side}</Pill></td>
                    <td className={clsx("num text-right", tone(t.ret))}>{pct(t.ret, 1)}</td>
                  </tr>))}</tbody>
              </table>
              {!(perf.recent_trades ?? []).length && <div className="py-6 text-center text-[12px] text-ink-500">No trades taken on this name.</div>}
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
