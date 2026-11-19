import clsx from "clsx";
import { ArrowDownRight, ArrowUpRight, CalendarClock, Check, ChevronRight, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { chartTooltip, ErrorBox, Loading, Pill, SentDot } from "../components/ui";
import { useApi } from "../lib/api";
import { compact, num, pct, tone, usd } from "../lib/format";
import type { AccuracyData, AccuracyRow, Backtest, Brief, DataUsed, Idea, Market, TickerProfile } from "../lib/types";

type Reliability = { level: "Low" | "Moderate" | "High"; color: "down" | "amber" | "up"; text: string };

function reliability(bt: Backtest | null): Reliability | null {
  const s = bt?.strategies.stock_daytrade;
  const dsr = s?.robustness?.dsr;
  if (!s || dsr == null) return null;
  const tail = `Backtest of this exact book since ${bt!.period.start.slice(0, 4)}: ${pct(s.cagr, 0)} a year after costs, worst drop ${pct(s.max_drawdown, 0)}.`;
  if (dsr >= 0.95) return { level: "High", color: "up", text: `The edge has held up after costs. ${tail}` };
  if (dsr >= 0.5) return { level: "Moderate", color: "amber", text: `The edge is positive but not yet proven. ${tail}` };
  return { level: "Low", color: "down", text: `The ranking has some skill, but it has not been reliably profitable after costs. ${tail}` };
}

export default function Today() {
  const { data, error } = useApi<Brief>("brief");
  const { data: acc } = useApi<AccuracyData>("accuracy");
  const { data: bt } = useApi<Backtest>("backtest");
  const { data: mkt } = useApi<Market>("market");
  const [open, setOpen] = useState<Idea | null>(null);
  const track = useMemo(() => new Map((acc?.stocks ?? []).map((r) => [r.ticker, r])), [acc]);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const longs = data.longs ?? [], shorts = data.shorts ?? [];
  const day = new Date(data.scan_date + "T12:00:00");
  const rel = reliability(bt);
  const optionIdeas = [...longs, ...shorts].filter((i) => i.option?.worth_it);
  const settled = [...longs, ...shorts].filter((i) => i.outcome);
  const right = settled.filter((i) => i.outcome!.correct).length;
  const w = data.world;
  const newsRow = data.data_used?.find((d) => d.source === "Company news");
  const priceRow = data.data_used?.find((d) => d.source === "Daily prices");
  const hasPremarket = data.movers.some((m) => Math.abs(m.gap) > 1e-6);
  return (
    <div>
      {/* ---- the answer first ---- */}
      <section className="card p-6">
        <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
          <div className="min-w-[280px] flex-1">
            <div className="label">{day.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" })} · before the open</div>
            <h1 className="mt-2 text-[28px] font-semibold leading-tight tracking-tight text-ink-100">
              {longs.length} to buy, {shorts.length} to short
            </h1>
            <p className="mt-2 max-w-[68ch] text-[15px] leading-relaxed text-ink-300">
              The model ranked {data.universe_size} tradable stocks by how each should do against the market from today's open to the close.
              These are the strongest on each side. {optionIdeas.length ? `${optionIdeas.length} also clear option costs.` : "None clear option costs today, so shares only."}
            </p>
            {newsRow && (
              <p className={clsx("mt-3 inline-flex items-start gap-2 rounded-lg px-3 py-2 text-[14px]", newsRow.status === "ok" ? "bg-up/10 text-ink-200" : "bg-amber/10 text-ink-200")}>
                <span className={clsx("mt-1.5 h-2 w-2 shrink-0 rounded-full", newsRow.status === "ok" ? "bg-up" : "bg-amber")} />
                {newsRow.status === "ok" ? <span>News used: {newsRow.note}.</span>
                  : <span><b className="font-semibold text-ink-100">No news was used for these ideas.</b> Sofo World news is not connected, so the ranking rests on prices, filings and earnings through {fmtDay(priceRow?.through)}.</span>}
              </p>)}
          </div>
          {rel && (
            <div className="w-full max-w-[380px] rounded-xl border border-ink-700 bg-ink-850 p-4">
              <div className="flex items-center gap-2"><span className="label">How much to trust this</span><Pill color={rel.color}>{rel.level}</Pill></div>
              <p className="mt-2 text-[13.5px] leading-relaxed text-ink-300">{rel.text}</p>
              <Link to="/performance" className="mt-2 inline-flex items-center gap-1 text-[13.5px] font-medium text-accent hover:underline">See the evidence <ChevronRight className="h-4 w-4" /></Link>
            </div>
          )}
        </div>
        <div className="mt-5 grid grid-cols-2 gap-x-8 gap-y-3 border-t border-ink-700 pt-4 md:grid-cols-4">
          <Fact label="Market before the open" value={hasPremarket ? <span className={tone(data.market.premarket_gap)}>{pct(data.market.premarket_gap, 2)}</span> : "–"}
            note={hasPremarket ? "average premarket gap" : "premarket prices not loaded for this scan"} />
          <Fact label="Size per position" value={usd(data.book?.per_position)} note={`on ${usd(data.equity)}, 1× long and 1× short`} />
          <Fact label="Political and macro risk" value={w?.politics_risk == null ? "–" : w.politics_risk > 1.5 ? "Elevated" : w.politics_risk > 0.5 ? "Watch" : "Calm"}
            note={w?.vix == null ? "" : `VIX ${num(w.vix, 1)} · 10y ${w.dgs10_chg5 == null ? "–" : `${w.dgs10_chg5 > 0 ? "+" : ""}${(w.dgs10_chg5 * 100).toFixed(0)} bp`} this week`} />
          <Fact label={settled.length ? "How these ideas did" : "Result"} value={settled.length ? `${right} of ${settled.length} right` : "Pending"}
            note={settled.length ? "vs the market, this session" : "fills in after the close"} />
        </div>
      </section>
      {mkt && (
        <Link to="/market" className="card mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3.5 transition hover:border-accent/50">
          <span className="label">Market backdrop</span>
          <Pill color={mkt.macro.verdict.startsWith("More supports") ? "up" : mkt.macro.verdict.startsWith("More risks") ? "down" : "amber"}>{mkt.macro.verdict}</Pill>
          <span className="text-[14px] text-ink-200">{mkt.macro.bull.length} things helping stocks, {mkt.macro.risks.length} risks.</span>
          {mkt.macro.risks[0] && <span className="min-w-0 flex-1 truncate text-[13.5px] text-ink-400">Top risk: {mkt.macro.risks[0].text}</span>}
          {mkt.filings && <span className="text-[13.5px] text-ink-400">{mkt.filings.important.length} notable SEC filings on {fmtDay(mkt.filings.date)}</span>}
          <ChevronRight className="ml-auto h-4 w-4 text-ink-400" />
        </Link>)}


      {/* ---- the ideas ---- */}
      <div className="mt-5 grid grid-cols-1 gap-5 xl:grid-cols-2">
        <IdeaList title="Buy" tone="up" ideas={longs} track={track} onOpen={setOpen} openTicker={open?.ticker} />
        <IdeaList title="Short" tone="down" ideas={shorts} track={track} onOpen={setOpen} openTicker={open?.ticker} />
      </div>

      {/* ---- context ---- */}
      <div className="mt-5 grid grid-cols-1 gap-5 xl:grid-cols-3">
        <section className={clsx("card p-5", hasPremarket ? "xl:col-span-2" : "xl:col-span-3")}>
          <h2 className="text-[16px] font-semibold text-ink-100">Earnings before the next open</h2>
          <p className="mt-1 text-[13.5px] text-ink-400">What options expect each stock to move, against how much it has moved on its own past reports.</p>
          {data.straddles.length === 0 ? <p className="mt-4 text-[14px] text-ink-400">No tradable companies report before the next session.</p> : (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-[14px]">
                <thead><tr className="text-left text-[12.5px] text-ink-400">
                  <th className="py-2 font-medium">Stock</th><th className="font-medium">Options expect</th><th className="font-medium">Its own average</th><th className="font-medium">Read</th></tr></thead>
                <tbody>{data.straddles.map((s) => (
                  <tr key={s.ticker} className="border-t border-ink-700">
                    <td className="py-2.5"><Link to={`/t/${s.ticker}`} className="font-semibold text-ink-100 hover:text-accent">{s.ticker}</Link> <span className="text-[13px] text-ink-400">{s.name}</span></td>
                    <td className="num">±{pct(s.implied_move, 1, false)}</td>
                    <td className="num">{s.hist_move ? `±${pct(s.hist_move, 1, false)}` : "–"}</td>
                    <td>{s.edge_ratio == null ? <span className="text-ink-400">no history</span> : s.edge_ratio >= 1.15 ? <Pill color="up">moves more than priced</Pill> : s.edge_ratio < 0.8 ? <Pill color="down">moves less than priced</Pill> : <Pill>fairly priced</Pill>}</td>
                  </tr>))}</tbody>
              </table>
            </div>)}
        </section>
        {hasPremarket && <section className="card p-5">
          <h2 className="text-[16px] font-semibold text-ink-100">Biggest premarket moves</h2>
          <div className="mt-3 divide-y divide-ink-700">
            {data.movers.slice(0, 8).map((m) => (
              <Link key={m.ticker} to={`/t/${m.ticker}`} className="flex items-center gap-3 py-2 text-[14px] hover:bg-ink-800">
                <span className="w-14 font-semibold text-ink-100">{m.ticker}</span>
                <span className="min-w-0 flex-1 truncate text-[13px] text-ink-400">{m.name}</span>
                <span className={clsx("num", tone(m.gap))}>{pct(m.gap, 1)}</span>
              </Link>))}
          </div>
        </section>}
      </div>

      {data.data_used && <DataUsedTable rows={data.data_used} scan={data.scan_date} />}

      {open && <IdeaPanel idea={open} track={track.get(open.ticker)} equity={data.equity} onClose={() => setOpen(null)} />}
    </div>
  );
}

const fmtDay = (d?: string | null) => (d ? new Date(d + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" }) : "–");
const STATUS: Record<DataUsed["status"], { c: "up" | "down" | "amber" | "ink"; t: string }> = {
  ok: { c: "up", t: "Used" }, missing: { c: "down", t: "Not used" }, stale: { c: "amber", t: "Out of date" },
  estimated: { c: "amber", t: "Estimated" }, partial: { c: "amber", t: "Partly" }, context: { c: "ink", t: "Context only" },
};

function DataUsedTable({ rows, scan }: { rows: DataUsed[]; scan: string }) {
  return (
    <section className="card mt-5 overflow-hidden">
      <header className="border-b border-ink-700 px-5 py-3.5">
        <h2 className="text-[16px] font-semibold text-ink-100">Data behind these ideas</h2>
        <p className="mt-0.5 text-[13.5px] text-ink-400">Everything the model saw for the {fmtDay(scan)} session, and the last date each source covers. Nothing dated after 09:00 New York time on the scan day is used.</p>
      </header>
      <div className="overflow-x-auto">
        <table className="w-full text-[14px]">
          <thead><tr className="text-left text-[12.5px] text-ink-400">
            <th className="px-5 py-2.5 font-medium">Source</th><th className="font-medium">Status</th><th className="font-medium">Covers through</th><th className="px-5 font-medium">Detail</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.source} className="border-t border-ink-700 align-top">
              <td className="px-5 py-2.5"><span className="font-medium text-ink-100">{r.source}</span><div className="text-[13px] text-ink-400">{r.what}</div></td>
              <td className="py-2.5"><Pill color={STATUS[r.status].c}>{STATUS[r.status].t}</Pill></td>
              <td className="num py-2.5 text-ink-100">{fmtDay(r.through)}</td>
              <td className="px-5 py-2.5 text-[13.5px] text-ink-300">{r.note || "–"}</td>
            </tr>))}</tbody>
        </table>
      </div>
    </section>
  );
}

function Fact({ label, value, note }: { label: string; value: React.ReactNode; note?: string }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className="num mt-1 text-[19px] font-semibold text-ink-100">{value}</div>
      {note && <div className="text-[12.5px] text-ink-400">{note}</div>}
    </div>
  );
}

function IdeaList({ title, tone: t, ideas, track, onOpen, openTicker }: {
  title: string; tone: "up" | "down"; ideas: Idea[]; track: Map<string, AccuracyRow>; onOpen: (i: Idea) => void; openTicker?: string;
}) {
  const Icon = t === "up" ? ArrowUpRight : ArrowDownRight;
  return (
    <section className="card overflow-hidden">
      <header className="flex items-center gap-2 border-b border-ink-700 px-5 py-3">
        <span className={clsx("grid h-7 w-7 place-items-center rounded-lg", t === "up" ? "bg-up/15 text-up" : "bg-down/15 text-down")}><Icon className="h-4 w-4" /></span>
        <h2 className="text-[16px] font-semibold text-ink-100">{title}</h2>
        <span className="text-[13px] text-ink-400">{t === "up" ? "expected to beat the market today" : "expected to lag the market today"}</span>
      </header>
      {ideas.length === 0 && <p className="px-5 py-6 text-[14px] text-ink-400">No ideas on this side today.</p>}
      <ol>
        {ideas.map((i, k) => {
          const tr = track.get(i.ticker);
          const main = i.why.find((x) => x.helps) ?? i.why[0];
          return (
            <li key={i.ticker} className="border-b border-ink-700 last:border-0">
              <button onClick={() => onOpen(i)} className={clsx("row-btn grid grid-cols-[22px_1fr_auto] items-center gap-3 px-5 py-3", openTicker === i.ticker && "bg-ink-800")}>
                <span className="num text-[13px] text-ink-500">{k + 1}</span>
                <span className="min-w-0">
                  <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="text-[16px] font-semibold text-ink-100">{i.ticker}</span>
                    <span className="truncate text-[13px] text-ink-400">{i.name}</span>
                    <Pill color={i.strength === "Strong" ? t : i.strength === "Solid" ? "accent" : "ink"}>{i.strength}</Pill>
                    {i.earnings_today && <Pill color="amber"><CalendarClock className="h-3 w-3" />earnings</Pill>}
                    {i.option?.worth_it && <Pill color="sky">option ok</Pill>}
                  </span>
                  <span className="mt-1 block truncate text-[13.5px] text-ink-300">
                    {main ? main.label : "Model ranking"}
                    {tr?.strong_accuracy != null && <span className="text-ink-400"> · right {pct(tr.strong_accuracy, 0, false)} of {tr.strong_n} past strong calls</span>}
                  </span>
                </span>
                <span className="flex items-center gap-3 text-right">
                  {i.outcome && (
                    <span className={clsx("num flex items-center gap-1 text-[13.5px]", i.outcome.correct ? "text-up" : "text-down")}>
                      {i.outcome.correct ? <Check className="h-3.5 w-3.5" /> : <X className="h-3.5 w-3.5" />}{pct((i.action === "BUY" ? 1 : -1) * i.outcome.rel_move, 1)}
                    </span>)}
                  <span>
                    <span className="num block text-[14px] text-ink-100">±{pct(i.exp_move_pct, 1, false)}</span>
                    <span className="block text-[12px] text-ink-400">typical move</span>
                  </span>
                  <ChevronRight className="h-4 w-4 text-ink-500" />
                </span>
              </button>
            </li>);
        })}
      </ol>
    </section>
  );
}

function IdeaPanel({ idea: i, track, equity, onClose }: { idea: Idea; track?: AccuracyRow; equity: number; onClose: () => void }) {
  const { data: prof } = useApi<TickerProfile>(`ticker/${i.ticker}`);
  const buy = i.action === "BUY";
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  const a = prof?.accuracy;
  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-label={`${i.ticker} idea`}>
      <button className="absolute inset-0 bg-black/30" onClick={onClose} aria-label="Close" />
      <aside className="relative h-full w-full max-w-[520px] overflow-y-auto border-l border-ink-700 bg-ink-900 p-6 shadow-2xl fade-in">
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <Pill color={buy ? "up" : "down"}>{buy ? "Buy" : "Short"}</Pill><Pill color={i.strength === "Strong" ? (buy ? "up" : "down") : "ink"}>{i.strength}</Pill>
              {i.earnings_today && <Pill color="amber">earnings reaction day</Pill>}
            </div>
            <h2 className="mt-2 text-[26px] font-semibold tracking-tight text-ink-100">{i.ticker}</h2>
            <p className="text-[14px] text-ink-400">{i.name} · {i.sector}</p>
          </div>
          <button onClick={onClose} className="rounded-lg border border-ink-600 p-1.5 text-ink-300 hover:text-ink-100" aria-label="Close"><X className="h-4 w-4" /></button>
        </div>

        <Block title="What to do">
          <p className="text-[15px] text-ink-100">{i.plan}</p>
          <dl className="mt-3 grid grid-cols-3 gap-3">
            <KV k="Shares" v={i.shares.toLocaleString()} n={`${compact(i.notional, "$")} of ${compact(equity, "$")}`} />
            <KV k="Reference price" v={usd(i.ref_price, 2)} n={i.premarket_gap == null ? "" : `premarket ${pct(i.premarket_gap, 1)}`} />
            <KV k="Typical move" v={`±${pct(i.exp_move_pct, 1, false)}`} n="open to close" />
          </dl>
          {i.outcome && (
            <p className={clsx("mt-3 rounded-lg px-3 py-2 text-[14px]", i.outcome.correct ? "bg-up/10 text-up" : "bg-down/10 text-down")}>
              Result: stock {pct(i.outcome.stock_move, 2)}, {pct(i.outcome.rel_move, 2)} against the market. The call was {i.outcome.correct ? "right" : "wrong"}.
            </p>)}
        </Block>

        <Block title="Why the model ranks it here">
          <ul className="space-y-2">
            {i.why.map((w) => (
              <li key={w.key} className="grid grid-cols-[1fr_120px_64px] items-center gap-3 text-[14px]">
                <span className="text-ink-200">{w.label}</span>
                <span className="h-1.5 rounded-full bg-ink-800"><span className={clsx("block h-full rounded-full", w.helps ? (buy ? "bg-up" : "bg-down") : "bg-ink-500")} style={{ width: `${Math.min(100, w.share * 250)}%` }} /></span>
                <span className={clsx("text-right text-[12.5px]", w.helps ? "text-ink-200" : "text-ink-400")}>{w.helps ? "supports" : "against"}</span>
              </li>))}
          </ul>
          <p className="mt-3 text-[12.5px] text-ink-400">Ranked in the {buy ? "top" : "bottom"} {Math.max(1, Math.round((buy ? 1 - i.score_pct : i.score_pct) * 100))}% of tradable stocks today.</p>
        </Block>

        <Block title="Track record on this stock">
          {track ? (
            <dl className="grid grid-cols-3 gap-3">
              <KV k="Strong calls right" v={track.strong_accuracy == null ? "–" : pct(track.strong_accuracy, 0, false)} n={`${track.strong_n} past calls`} />
              <KV k="All days right" v={pct(track.rel_accuracy, 1, false)} n={`${track.days.toLocaleString()} days`} />
              <KV k="Edge per day" v={`${num(track.signal_bps, 1)} bps`} n="before costs" />
            </dl>) : <p className="text-[14px] text-ink-400">Not enough history for this stock.</p>}
          {a && (
            <div className="mt-3 h-[150px]">
              <ResponsiveContainer>
                <LineChart data={a.curve} margin={{ left: -8, right: 6, top: 6 }}>
                  <XAxis dataKey="d" tickLine={false} axisLine={false} minTickGap={80} />
                  <YAxis tickFormatter={(v) => pct(v, 0)} tickLine={false} axisLine={false} />
                  <Tooltip {...chartTooltip} formatter={(v: number) => [pct(v, 1), "following the model"]} />
                  <ReferenceLine y={0} stroke="var(--color-ink-500)" />
                  <Line dataKey="signal" stroke="var(--color-accent)" dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </div>)}
          {a && <p className="text-[12.5px] text-ink-400">Cumulative result of following every model call on {i.ticker}, against the market, before costs.</p>}
        </Block>

        <Block title="Options">
          {i.option ? (
            <>
              <p className="text-[15px] text-ink-100"><span className="num font-semibold">{i.option.contract}</span> at about {usd(i.option.premium, 2)}</p>
              <p className="mt-1 text-[14px] text-ink-300">
                {i.option.worth_it
                  ? `Clears costs: expected ${pct(i.option.ev, 0)} on premium, ${pct(i.option.p_profit, 0, false)} chance of profit. Up to ${i.option.contracts} contracts at 2% risk.`
                  : `Not worth it today: expected ${pct(i.option.ev, 0)} on premium after spread and a day of time decay. Use shares.`}
              </p>
            </>) : <p className="text-[14px] text-ink-400">No option priced for this side today. Use shares.</p>}
        </Block>

        {i.headlines.length > 0 && (
          <Block title="News since the last close">
            <ul className="space-y-2">{i.headlines.map((h, k) => (
              <li key={k} className="flex items-start gap-2 text-[14px] text-ink-200"><span className="mt-1.5"><SentDot s={h.sentiment} /></span>
                <span>{h.headline}<span className="num block text-[12.5px] text-ink-400">{h.time} ET{h.source ? ` · ${h.source}` : ""}</span></span></li>))}</ul>
          </Block>)}

        <Link to={`/t/${i.ticker}`} className="mt-6 inline-flex items-center gap-1 text-[14px] font-medium text-accent hover:underline">Open the full {i.ticker} page <ChevronRight className="h-4 w-4" /></Link>
      </aside>
    </div>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="mt-6 border-t border-ink-700 pt-4"><h3 className="label mb-2.5">{title}</h3>{children}</section>;
}
function KV({ k, v, n }: { k: string; v: React.ReactNode; n?: string }) {
  return <div><dt className="text-[12.5px] text-ink-400">{k}</dt><dd className="num text-[17px] font-semibold text-ink-100">{v}</dd>{n && <div className="text-[12px] text-ink-400">{n}</div>}</div>;
}
