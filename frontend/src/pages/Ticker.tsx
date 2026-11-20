import clsx from "clsx";
import { ArrowLeft, Brain, CalendarClock, ExternalLink, FileText, Newspaper } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import CandleChart from "../components/CandleChart";
import { Card, chartTooltip, ErrorBox, Loading, PageHeader, Pill, SentDot, Stat, Tabs, ToneDot, TonePill } from "../components/ui";
import { STATIC, useApi, useLive } from "../lib/api";
import { compact, num, pct, tone, TRAIT_LABEL, usd } from "../lib/format";
import type { ChainRow, Light, LiveQuote, OptionChain, TickerProfile } from "../lib/types";

function Full({ data }: { data: TickerProfile }) {
  const [range, setRange] = useState<"60" | "120" | "260">("120");
  const [newsFilter, setNewsFilter] = useState<"all" | "material">("material");
  const px = data.prices.slice(-Number(range));
  const perf = data.performance;
  const traitRows = Object.entries(data.model);
  const news = data.news.filter((n) => newsFilter === "all" || n.m >= 0.4);
  return (
    <div>
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
          right={<span className="text-[12.5px] text-ink-500">effective coefficient = market + this ticker's shrunk trait</span>}>
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
                    <span className="num text-[12.5px] text-ink-500">{pct(conf, 0, false)} own data</span>
                  </div>
                  <div className="mt-1 text-[13px] text-ink-400">{data.ticker} {m.effective >= 0 ? L?.pos : L?.neg}{Math.abs(m.effective) > Math.abs(mkt) * 1.25 ? ", more than the market" : Math.abs(m.effective) < Math.abs(mkt) * 0.75 ? ", less than the market" : ", in line with the market"}.</div>
                  <div className="mt-3 space-y-2 text-[12.5px]">
                    {[{ l: "Market", v: mkt, c: "var(--color-ink-400)" }, { l: data.ticker, v: m.effective, c: m.effective >= 0 ? "var(--color-up)" : "var(--color-down)" }].map((b) => (
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
                <Legend formatter={(v) => <span className="text-[12.5px] text-ink-400">{TRAIT_LABEL[v]?.name ?? v}</span>} />
                <ReferenceLine y={0} stroke="var(--color-ink-500)" />
                <Line dataKey="news_on_wsent" stroke="var(--color-accent)" dot={false} strokeWidth={1.8} />
                <Line dataKey="gap_pre_z" stroke="var(--color-sky)" dot={false} strokeWidth={1.4} />
                <Line dataKey="ret_1d_z" stroke="var(--color-amber)" dot={false} strokeWidth={1.4} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card title={<span className="flex items-center gap-2"><CalendarClock className="h-4 w-4 text-amber" />Earnings: implied vs actual</span>}>
          {data.earnings.length === 0 ? <div className="py-8 text-center text-[13px] text-ink-500">No earnings history yet.</div> : (<>
            <div className="h-[210px]">
              <ResponsiveContainer>
                <BarChart data={data.earnings.slice(-12)} margin={{ left: -14 }}>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="d" tickFormatter={(d) => d.slice(2, 7)} tickLine={false} axisLine={false} />
                  <YAxis tickFormatter={(v) => pct(v, 0, false)} tickLine={false} axisLine={false} />
                  <Tooltip {...chartTooltip} formatter={(v: number, n: string) => [pct(v, 1, false), n]} />
                  <Bar dataKey="implied" fill="var(--color-ink-500)" radius={[3, 3, 0, 0]} name="implied" />
                  <Bar dataKey="actual" radius={[3, 3, 0, 0]} name="actual">{data.earnings.slice(-12).map((e, i) => <Cell key={i} fill={e.signed >= 0 ? "var(--color-up)" : "var(--color-down)"} />)}</Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            {(() => {
              const beat = data.earnings.filter((e) => e.actual > e.implied).length;
              const avgI = data.earnings.reduce((a, e) => a + e.implied, 0) / data.earnings.length;
              const avgA = data.earnings.reduce((a, e) => a + e.actual, 0) / data.earnings.length;
              return <div className="mt-3 grid grid-cols-3 gap-2 text-center text-[12.5px]">
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
                  <div className="mt-0.5 flex flex-wrap gap-x-3 text-[12.5px] text-ink-500">
                    <span className="num">{n.t} ET</span><span>{n.src}</span><span>{n.e}</span>
                    <span>{n.on ? `overnight → ${n.d} open` : `intraday, priced by ${n.d}`}</span>
                  </div>
                </div>
                <span className={clsx("num shrink-0 text-[13px]", tone(n.s))}>{n.s > 0 ? "+" : ""}{n.s.toFixed(2)}</span>
              </div>))}
          </div>
        </Card>
        <Card title="Trading record on this ticker" pad={false}>
          <div className="px-5 pb-4">
            <div className="mb-3 grid grid-cols-3 gap-2 text-[12.5px]">
              {Object.entries(perf.trades ?? {}).map(([k, v]) => (
                <div key={k} className="rounded-lg border border-ink-800 p-2">
                  <div className="truncate text-ink-500">{k.replace("_", " ")}</div>
                  <div className="num text-[13px]">{v.n} · {pct(v.win, 0, false)}</div>
                  <div className={clsx("num", tone(v.pnl))}>{usd(v.pnl)}</div>
                </div>))}
            </div>
            <div className="max-h-[300px] overflow-auto">
              <table className="w-full text-[12.5px]">
                <tbody>{(perf.recent_trades ?? []).slice().reverse().map((t, i) => (
                  <tr key={i} className="border-t border-ink-800/70">
                    <td className="num py-1.5 text-ink-400">{t.date}</td>
                    <td><Pill color={["CALL", "LONG"].includes(t.side) ? "up" : t.side === "STRADDLE" ? "amber" : "down"}>{t.side}</Pill></td>
                    <td className={clsx("num text-right", tone(t.ret))}>{pct(t.ret, 1)}</td>
                  </tr>))}</tbody>
              </table>
              {!(perf.recent_trades ?? []).length && <div className="py-6 text-center text-[13px] text-ink-500">No trades taken on this name.</div>}
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}

function LiveCard({ sym }: { sym: string }) {
  const [expiry, setExpiry] = useState<string | null>(null);
  const { data: qs } = useLive<Record<string, LiveQuote>>(`live/quotes?symbols=${sym}`, 30000);
  const { data: ch, error } = useLive<OptionChain>(`live/options/${sym}${expiry ? `?expiry=${expiry}` : ""}`, 90000);
  const q = qs?.[sym];
  const rows = ch ? ch.calls.map((c) => ({ c, p: ch.puts.find((x) => x.strike === c.strike) })) : [];
  const cell = (r?: ChainRow) => (r ? <>
    <td className="num px-2 py-1.5 text-right">{r.bid == null ? "–" : r.bid.toFixed(2)}</td>
    <td className="num px-2 text-right">{r.ask == null ? "–" : r.ask.toFixed(2)}</td>
    <td className="num px-2 text-right text-ink-400">{r.iv == null ? "–" : pct(r.iv, 0, false)}</td>
    <td className="num px-2 text-right text-ink-400">{r.volume.toLocaleString()}</td></> : <td colSpan={4} />);
  return (
    <Card className="mb-4" title={<span className="flex items-center gap-2"><span className={clsx("h-2 w-2 rounded-full", q?.state === "open" ? "animate-pulse bg-up" : "bg-ink-400")} />
      Live price and options</span>}
      right={<span className="text-[12.5px] text-ink-400">Yahoo quotes, can lag up to 15 min · refreshes on its own</span>}>
      {q ? (
        <div className="flex flex-wrap items-baseline gap-x-8 gap-y-2">
          <span className="num text-[30px] font-semibold text-ink-100">{usd(q.price, 2)}</span>
          <span className="text-[14px] text-ink-300">today <b className={clsx("num", tone(q.chg_day))}>{pct(q.chg_day, 2)}</b></span>
          <span className="text-[14px] text-ink-300">since the open <b className={clsx("num", tone(q.chg_open))}>{pct(q.chg_open, 2)}</b></span>
          <span className="text-[14px] text-ink-400">range {usd(q.low, 2)} to {usd(q.high, 2)} · {compact(q.volume)} shares</span>
          <span className="text-[13px] text-ink-400">{q.state === "open" ? "market open" : q.state === "pre" ? "before the open" : "market closed"}{q.time && ` · ${new Date(q.time).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`}</span>
        </div>) : <div className="text-[14px] text-ink-400">Fetching the live price…</div>}
      {ch ? (<>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="label">Options expiring</span>
          {ch.expiries.slice(0, 8).map((e) => (
            <button key={e} onClick={() => setExpiry(e)} className={clsx("rounded-lg border px-2.5 py-1 text-[12.5px]", e === ch.expiry ? "border-accent bg-accent/10 text-ink-100" : "border-ink-700 text-ink-400 hover:text-ink-100")}>{day(e)}</button>))}
        </div>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead><tr className="text-ink-500">
              <th colSpan={4} className="border-b border-ink-700 px-2 py-1.5 text-center font-medium text-up">Calls (profit if the stock rises)</th>
              <th className="border-b border-ink-700 px-3 font-medium">Strike</th>
              <th colSpan={4} className="border-b border-ink-700 px-2 text-center font-medium text-down">Puts (profit if the stock falls)</th></tr>
            <tr className="text-ink-500">{["Bid", "Ask", "Implied vol.", "Volume"].map((h) => <th key={h} className="px-2 py-1 text-right font-medium">{h}</th>)}<th />
              {["Bid", "Ask", "Implied vol.", "Volume"].map((h) => <th key={"p" + h} className="px-2 text-right font-medium">{h}</th>)}</tr></thead>
            <tbody>{rows.map(({ c, p }) => (
              <tr key={c.strike} className={clsx("border-t border-ink-800/70", ch.spot != null && Math.abs(c.strike - ch.spot) === Math.min(...rows.map((r) => Math.abs(r.c.strike - ch.spot!))) && "bg-accent/10")}>
                {cell(c)}<td className="num px-3 text-center font-semibold text-ink-100">{c.strike}</td>{cell(p)}</tr>))}</tbody>
          </table>
        </div>
        <p className="mt-2 text-[12.5px] text-ink-500">Prices are per share; one contract covers 100 shares. The highlighted row is the strike closest to the current price. Bid is what you can sell at, ask is what you pay.</p>
      </>) : <div className="mt-3 text-[13.5px] text-ink-400">{error ? "No listed options found for this ticker, or the quote service is busy." : "Fetching the option chain…"}</div>}
    </Card>
  );
}

const day = (s: string) => new Date(s.length > 10 ? s : s + "T12:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });

export default function Ticker() {
  const { sym = "" } = useParams();
  const S = sym.toUpperCase();
  const { data: shard, error } = useApi<Record<string, Light> & { _dates: string[] }>(`light/${/^[A-Z]/.test(S) ? S[0] : "0"}`);
  const { data: full } = useApi<TickerProfile>(`ticker/${S}`);
  if (error) return <ErrorBox error={error} />;
  if (!shard) return <Loading />;
  const d = shard[S];
  if (!d) return <ErrorBox error={`${S} is not a ticker the SEC lists. Check the symbol, or search by company name on the Stocks page.`} />;
  const series = d.closes.map((c, i) => ({ d: shard._dates[i], c })).filter((x): x is { d: string; c: number } => x.c != null);
  const up = series.length > 1 && series[series.length - 1].c >= series[0].c;
  return (
    <div>
      <Link to="/stocks" className="mb-3 inline-flex items-center gap-1 text-[13.5px] text-ink-400 hover:text-ink-100"><ArrowLeft className="h-3.5 w-3.5" />Stocks</Link>
      <PageHeader
        title={<span className="flex flex-wrap items-center gap-3">{d.ticker}<span className="text-[15px] font-normal text-ink-400">{d.name}</span></span>}
        sub={<span className="flex flex-wrap items-center gap-2">{d.exchange}{d.sector && ` · ${d.sector}`}
          {d.modelled ? <Pill color="accent">Model follows this stock</Pill> : <Pill>Tracked: price, filings and news only</Pill>}
          {d.model?.action && <Pill color={d.model.action === "BUY" ? "up" : "down"}>{d.model.action === "BUY" ? "Buy idea today" : "Short idea today"}</Pill>}</span>}
        right={d.price != null ? <div className="text-right"><div className="num text-[28px] font-semibold">{usd(d.price, 2)}</div>
          <div className={clsx("num text-[13px]", tone(d.chg.d1))}>{pct(d.chg.d1, 1)} on {day(d.as_of!)}</div></div> : <Pill color="amber">No current price</Pill>} />

      {!STATIC && <LiveCard sym={S} />}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.25fr_1fr]">
        <Card title="In plain words">
          <p className="text-[16px] font-medium leading-snug text-ink-100">{d.summary.line}</p>
          <ul className="mt-3 space-y-2.5">
            {d.summary.points.map((p, i) => (
              <li key={i} className="flex gap-2.5 text-[14px] leading-snug text-ink-200"><ToneDot t={p.tone} /><span>{p.text}</span></li>))}
          </ul>
          {!d.summary.points.length && <p className="mt-2 text-[14px] text-ink-400">Nothing notable on record for this ticker in the past 45 days.</p>}
          <p className="mt-4 text-[12.5px] text-ink-500">Written by fixed rules from prices, SEC filings and headlines. Green helps, red hurts, amber is worth a look, grey is routine.</p>
        </Card>
        <Card title="Price, past 6 months">
          {series.length > 1 ? (<>
            <div className="h-[200px]">
              <ResponsiveContainer>
                <LineChart data={series} margin={{ left: -8, right: 8 }}>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="d" tickLine={false} axisLine={false} minTickGap={60} tickFormatter={(v) => day(v)} />
                  <YAxis domain={["auto", "auto"]} tickLine={false} axisLine={false} width={52} />
                  <Tooltip {...chartTooltip} formatter={(v: number) => [usd(v, 2), "close"]} />
                  <Line dataKey="c" stroke={up ? "var(--color-up)" : "var(--color-down)"} dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-3 grid grid-cols-4 gap-2 text-center text-[12.5px]">
              {([["Week", d.chg.w1], ["Month", d.chg.m1], ["3 months", d.chg.m3], ["Year", d.chg.y1]] as const).map(([l, v]) => (
                <div key={l}><div className="text-ink-500">{l}</div><div className={clsx("num text-[14px]", tone(v))}>{pct(v, 0)}</div></div>))}
            </div>
          </>) : <div className="grid h-[200px] place-items-center text-[14px] text-ink-400">No recent trading on record.</div>}
        </Card>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title={<span className="flex items-center gap-2"><FileText className="h-4 w-4 text-accent" />SEC filings, past 45 days ({d.filings.length})</span>} pad={false}>
          <ul className="max-h-[440px] divide-y divide-ink-800 overflow-auto px-5 pb-3">
            {d.filings.map((f, i) => (
              <li key={i} className="py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="num text-[12.5px] text-ink-400">{day(f.d)}</span>
                  <span className="text-[14px] font-medium text-ink-100">{f.title}</span>
                  {f.imp >= 2 && <TonePill t={f.tone} />}
                  <a href={f.url} target="_blank" rel="noreferrer" className="ml-auto inline-flex items-center gap-1 text-[12.5px] text-ink-400 hover:text-accent">Form {f.form}<ExternalLink className="h-3 w-3" /></a>
                </div>
                <div className="mt-1 text-[13px] text-ink-400">{f.why}</div>
              </li>))}
            {!d.filings.length && <li className="py-8 text-center text-[14px] text-ink-400">No filings in the past 45 days.</li>}
          </ul>
        </Card>
        <Card title={<span className="flex items-center gap-2"><Newspaper className="h-4 w-4 text-sky" />News, past 10 days ({d.news.length})</span>} pad={false}>
          <ul className="max-h-[440px] divide-y divide-ink-800 overflow-auto px-5 pb-3">
            {d.news.map((n, i) => (
              <li key={i} className="flex gap-2.5 py-3"><ToneDot t={n.tone} />
                <a href={n.url} target="_blank" rel="noreferrer" className="text-[14px] leading-snug text-ink-100 hover:text-accent">{n.h}
                  <div className="mt-0.5 text-[12.5px] text-ink-500">{n.src} · {new Date(n.t).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</div></a>
              </li>))}
            {!d.news.length && <li className="py-8 text-center text-[14px] text-ink-400">{d.modelled ? "No stories tagged to this ticker in the past 10 days." : "Headlines are collected for the stocks the model follows; this one is not among them yet."}</li>}
          </ul>
        </Card>
      </div>

      {full && (
        <details className="mt-6">
          <summary className="cursor-pointer text-[15px] font-semibold text-ink-100">Model detail for {d.ticker}: accuracy, behaviour, earnings history</summary>
          <div className="mt-4"><Full data={full} /></div>
        </details>)}
    </div>
  );
}
