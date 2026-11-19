import clsx from "clsx";
import { ArrowDown, ArrowUp, X } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, chartTooltip, ErrorBox, Loading, PageHeader, Pill, Stat, Tabs } from "../components/ui";
import { useApi } from "../lib/api";
import { num, pct, tone } from "../lib/format";
import type { AccuracyData, AccuracyDetail, AccuracyRow, FundHorizon, IndexFund, TickerProfile, UniverseRow } from "../lib/types";

const accTone = (a: number | null | undefined, z?: number | null) =>
  a == null ? "text-ink-500" : z != null && Math.abs(z) < 2 ? "text-ink-200" : a > 0.5 ? "text-up" : "text-down";

type Row = AccuracyRow & { price?: number; chg?: number; days_to_earn?: number };
type SortKey = keyof Row;
const COLS: { k: SortKey; label: string; hint: string; fmt: (r: Row) => React.ReactNode; cls?: (r: Row) => string }[] = [
  { k: "price", label: "Price", hint: "last close", fmt: (r) => (r.price == null ? "–" : num(r.price, 2)) },
  { k: "chg", label: "Last day", hint: "last session's move", fmt: (r) => pct(r.chg, 1), cls: (r) => tone(r.chg) },
  { k: "strong_accuracy", label: "Strong calls right", hint: "how often the model was right on days this stock ranked in its top or bottom 10% (the days it would be traded)", fmt: (r) => (r.strong_accuracy == null ? "–" : pct(r.strong_accuracy, 0, false)), cls: (r) => accTone(r.strong_accuracy) },
  { k: "strong_n", label: "Strong calls", hint: "number of those days", fmt: (r) => r.strong_n },
  { k: "rel_accuracy", label: "All days right", hint: "share of all days the model called the stock's move against the market correctly", fmt: (r) => pct(r.rel_accuracy, 1, false), cls: (r) => accTone(r.rel_accuracy, r.z) },
  { k: "signal_bps", label: "Edge per day", hint: "average daily return of following the model on this stock, in basis points, before costs", fmt: (r) => `${num(r.signal_bps, 1)} bps`, cls: (r) => tone(r.signal_bps) },
  { k: "z", label: "Confidence", hint: "how far the record is from a coin flip, in standard errors. Above 2 is unlikely to be luck for a single stock.", fmt: (r) => (Math.abs(r.z) >= 3 ? "high" : Math.abs(r.z) >= 2 ? "fair" : "low"), cls: (r) => (r.z >= 2 ? "text-up" : r.z <= -2 ? "text-down" : "text-ink-400") },
  { k: "days_to_earn", label: "Earnings", hint: "sessions until the next report", fmt: (r) => (r.days_to_earn != null && r.days_to_earn < 900 ? `${r.days_to_earn}d` : "–"), cls: (r) => (r.days_to_earn != null && r.days_to_earn <= 5 ? "text-amber" : "") },
];

export default function Stocks() {
  const { data, error } = useApi<AccuracyData>("accuracy");
  const { data: uni } = useApi<UniverseRow[]>("universe");
  const [tab, setTab] = useState<"stocks" | "index">("stocks");
  const [q, setQ] = useState("");
  const [liquidOnly, setLiquidOnly] = useState(true);
  const [sort, setSort] = useState<{ k: SortKey; d: 1 | -1 }>({ k: "strong_accuracy", d: -1 });
  const [sel, setSel] = useState<string | null>(null);
  const [fund, setFund] = useState<string | null>("SPY");
  const rows = useMemo(() => {
    if (!data) return [];
    const u = new Map((uni ?? []).map((x) => [x.ticker, x]));
    const ql = q.toLowerCase();
    return data.stocks
      .map((r): Row => ({ ...r, price: u.get(r.ticker)?.price, chg: u.get(r.ticker)?.chg, days_to_earn: u.get(r.ticker)?.days_to_earn }))
      .filter((r) => (!liquidOnly || r.liquid_share > 0.5) && (!ql || r.ticker.toLowerCase().includes(ql) || (r.name ?? "").toLowerCase().includes(ql)))
      .sort((a, b) => (((a[sort.k] as number) ?? -1e9) > ((b[sort.k] as number) ?? -1e9) ? sort.d : -sort.d));
  }, [data, uni, q, liquidOnly, sort]);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const s = data.summary;
  const hist = s.hist.map((c, i) => ({ c, x: (s.hist_edges[i] + s.hist_edges[i + 1]) / 2 }));
  return (
    <div>
      <PageHeader title="Stocks"
        sub={`Every stock the model follows, with how often it has been right on that stock. All figures are out-of-sample, ${data.period.start} to ${data.period.end}. Click a row for the detail.`}
        right={<Tabs value={tab} onChange={setTab} items={[{ v: "stocks", label: `Stocks (${data.stocks.length.toLocaleString()})` }, { v: "index", label: `Index funds (${data.index?.funds.length ?? 0})` }]} />} />

      {tab === "stocks" ? (<>
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <Stat label="Typical stock, strong calls" value={pct(s.strong_median_accuracy, 1, false)} sub="right, on the days it would be traded. A coin flip is 50%." valueClass={s.strong_median_accuracy > 0.5 ? "text-up" : "text-down"} />
          <Stat label="Typical stock, all days" value={pct(s.median_accuracy, 1, false)} sub="right against the market" />
          <Stat label="Stocks it reads well" value={s.significant_good} sub={`clearly better than chance; about ${num(s.expected_by_chance, 0)} would be by luck`} valueClass="text-up" />
          <Stat label="Stocks it reads badly" value={s.significant_bad} sub="clearly worse than chance" />
        </div>

        <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[420px_1fr]">
          <Card title="How accurate, across all stocks">
            <div className="h-[220px]">
              <ResponsiveContainer>
                <BarChart data={hist} margin={{ left: -18 }}>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="x" tickFormatter={(v) => pct(v, 0, false)} tickLine={false} axisLine={false} />
                  <YAxis tickLine={false} axisLine={false} />
                  <Tooltip {...chartTooltip} formatter={(v: number) => [v, "stocks"]} labelFormatter={(l) => `accuracy ≈ ${pct(Number(l), 1, false)}`} />
                  <ReferenceLine x={0.5} stroke="var(--color-ink-300)" strokeDasharray="4 4" />
                  <Bar dataKey="c" radius={[3, 3, 0, 0]}>{hist.map((h, i) => <Cell key={i} fill={h.x >= 0.5 ? "var(--color-up)" : "var(--color-down)"} fillOpacity={0.55} />)}</Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <p className="mt-2 text-[13px] leading-relaxed text-ink-400">Each bar counts stocks by how often the model was right on them. With no skill the bars would centre on the dashed 50% line; they sit a little to its right.</p>
          </Card>
          {sel ? <StockDetail ticker={sel} onClose={() => setSel(null)} /> : (
            <Card title="Pick a stock">
              <div className="grid h-[250px] place-items-center text-center text-[14px] text-ink-400">
                <div>Click any row below to see how the model did on that stock:<br />the result of following it, and its accuracy year by year.</div>
              </div>
            </Card>)}
        </div>

        <div className="card mt-4 overflow-hidden">
          <div className="flex flex-wrap items-center gap-3 border-b border-ink-800 px-4 py-3">
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter ticker or name…" className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-1.5 text-[13px] outline-none focus:border-accent/60" />
            <Tabs value={liquidOnly ? "liquid" : "all"} onChange={(v) => setLiquidOnly(v === "liquid")} items={[{ v: "liquid", label: "Liquid" }, { v: "all", label: "All" }]} />
            <span className="ml-auto text-[13.5px] text-ink-400">{rows.length} stocks · click a column to sort</span>
          </div>
          <div className="max-h-[640px] overflow-auto">
            <table className="w-full text-[13.5px]">
              <thead className="sticky top-0 z-10 bg-ink-900/95 backdrop-blur"><tr className="text-left text-ink-500">
                <th className="px-4 py-2.5 font-medium">Stock</th>
                {COLS.map((c) => (
                  <th key={c.k} title={c.hint} onClick={() => setSort((p) => ({ k: c.k, d: p.k === c.k ? (-p.d as 1 | -1) : -1 }))}
                    className="cursor-pointer whitespace-nowrap px-2 text-right font-medium hover:text-ink-200">
                    <span className="inline-flex items-center gap-1">{c.label}{sort.k === c.k && (sort.d > 0 ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}</span>
                  </th>))}
              </tr></thead>
              <tbody>{rows.slice(0, 400).map((r) => (
                <tr key={r.ticker} onClick={() => setSel(r.ticker)}
                  className={clsx("cursor-pointer border-t border-ink-800/70 transition hover:bg-ink-800/50", sel === r.ticker && "bg-ink-800/70")}>
                  <td className="px-4 py-2"><span className="font-semibold">{r.ticker}</span>
                    <span className="ml-2 inline-block max-w-[200px] truncate align-bottom text-[12.5px] text-ink-500">{r.name}</span></td>
                  {COLS.map((c) => <td key={c.k} className={clsx("num px-2 text-right", c.cls?.(r))}>{c.fmt(r)}</td>)}
                </tr>))}</tbody>
            </table>
          </div>
        </div>
      </>) : data.index ? <IndexFunds funds={data.index.funds} period={data.index.period} sel={fund} setSel={setFund} /> :
        <Card><div className="py-8 text-center text-ink-400">Run <code className="num">python -m stockgpt.indexfunds</code> to build the index-fund backtest.</div></Card>}
    </div>
  );
}

function StockDetail({ ticker, onClose }: { ticker: string; onClose: () => void }) {
  const { data } = useApi<TickerProfile>(`ticker/${ticker}`);
  const a: AccuracyDetail | null | undefined = data?.accuracy;
  return (
    <Card title={<span className="flex items-center gap-2"><Link to={`/t/${ticker}`} className="text-[15px] font-semibold hover:text-accent">{ticker}</Link>
      <span className="text-[13px] font-normal text-ink-400">{data?.name}</span></span>}
      right={<button onClick={onClose} className="rounded-lg p-1 text-ink-400 hover:bg-ink-800 hover:text-ink-100"><X className="h-4 w-4" /></button>}>
      {!a ? <div className="grid h-[250px] place-items-center text-[13px] text-ink-500">{data ? "Not enough out-of-sample days for this stock." : "Loading…"}</div> : (
        <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
          <div>
            <div className="label mb-1">Cumulative return of following the signal (vs market, before costs) and of the stock itself</div>
            <div className="h-[190px]">
              <ResponsiveContainer>
                <LineChart data={a.curve} margin={{ left: -6, right: 8 }}>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="d" tickLine={false} axisLine={false} minTickGap={70} />
                  <YAxis tickFormatter={(v) => pct(v, 0)} tickLine={false} axisLine={false} />
                  <Tooltip {...chartTooltip} formatter={(v: number, n: string) => [pct(v, 1), n]} />
                  <ReferenceLine y={0} stroke="var(--color-ink-500)" />
                  <Line dataKey="signal" stroke="var(--color-accent)" dot={false} strokeWidth={2} name="following the model" />
                  <Line dataKey="stock" stroke="var(--color-ink-400)" dot={false} strokeWidth={1.2} strokeDasharray="4 3" name="stock open→close" />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-[13px] text-ink-400">
              <span>accuracy <b className={clsx("num", accTone(a.rel_accuracy, a.z))}>{pct(a.rel_accuracy, 1, false)}</b> over {a.days} days (z {num(a.z, 1)})</span>
              <span>strong-signal <b className={clsx("num", accTone(a.strong_accuracy))}>{a.strong_accuracy == null ? "–" : pct(a.strong_accuracy, 1, false)}</b> on {a.strong_n} days</span>
              <span>signal <b className={clsx("num", tone(a.signal_bps))}>{num(a.signal_bps, 1)} bps/day</b></span>
              {a.acc_5d != null && <span>5-day <b className={clsx("num", accTone(a.acc_5d, a.z_5d))}>{pct(a.acc_5d, 1, false)}</b></span>}
            </div>
          </div>
          <div>
            <div className="label mb-1">Accuracy by year</div>
            <div className="h-[190px]">
              <ResponsiveContainer>
                <BarChart data={a.by_year} margin={{ left: -18 }}>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="year" tickLine={false} axisLine={false} />
                  <YAxis domain={[0.4, 0.6]} tickFormatter={(v) => pct(v, 0, false)} tickLine={false} axisLine={false} />
                  <Tooltip {...chartTooltip} formatter={(v: number) => [pct(v, 1, false), "accuracy"]} />
                  <ReferenceLine y={0.5} stroke="var(--color-ink-300)" strokeDasharray="4 4" />
                  <Bar dataKey="accuracy" radius={[3, 3, 0, 0]}>{a.by_year.map((y, i) => <Cell key={i} fill={y.accuracy >= 0.5 ? "var(--color-up)" : "var(--color-down)"} fillOpacity={0.6} />)}</Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>)}
    </Card>
  );
}

function IndexFunds({ funds, period, sel, setSel }: { funds: IndexFund[]; period: { start: string; end: string }; sel: string | null; setSel: (s: string) => void }) {
  const [h, setH] = useState<"h1" | "h5">("h1");
  const f = funds.find((x) => x.ticker === sel) ?? funds[0];
  const d: FundHorizon = f[h];
  const beat = funds.filter((x) => x[h].accuracy > x[h].best_naive).length;
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Tabs value={h} onChange={setH} items={[{ v: "h1", label: "Next session (open → close)" }, { v: "h5", label: "Next 5 sessions" }]} />
        <span className="text-[13px] text-ink-400">{period.start} → {period.end} · one pooled model, refit every quarter · "naive" = always predicting the more common direction</span>
      </div>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Stat label="Funds beating naive" value={`${beat} / ${funds.length}`} sub="accuracy above always-up / always-down" valueClass={beat > funds.length / 2 ? "text-up" : "text-ink-100"} />
        <Stat label="Median accuracy" value={pct([...funds].map((x) => x[h].accuracy).sort()[Math.floor(funds.length / 2)], 1, false)} sub="across funds" />
        <Stat label="Median naive" value={pct([...funds].map((x) => x[h].best_naive).sort()[Math.floor(funds.length / 2)], 1, false)} sub="what always-up gets" />
        <Stat label={`${f.ticker} now`} value={pct(h === "h1" ? f.latest.p_up_1d : f.latest.p_up_5d, 1, false)} sub={`model P(up), as of ${f.latest.date}`} />
      </div>
      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[1fr_520px]">
        <div className="card overflow-hidden">
          <table className="w-full text-[13.5px]">
            <thead><tr className="text-left text-ink-500">
              <th className="px-4 py-2.5 font-medium">Fund</th><th className="px-2 text-right font-medium">Periods</th>
              <th className="px-2 text-right font-medium" title="share of periods called correctly">Accuracy</th>
              <th className="px-2 text-right font-medium" title="accuracy of always predicting the more common direction">Naive</th>
              <th className="px-2 text-right font-medium" title="standard errors above the naive rule">z vs naive</th>
              <th className="px-2 text-right font-medium" title="accuracy on the 25% most confident calls">Confident</th>
              <th className="px-2 text-right font-medium" title="annualised Sharpe of trading the signal long/short vs holding the fund over the same windows">Sharpe: signal / hold</th>
            </tr></thead>
            <tbody>{funds.map((x) => { const r = x[h]; return (
              <tr key={x.ticker} onClick={() => setSel(x.ticker)} className={clsx("cursor-pointer border-t border-ink-800/70 hover:bg-ink-800/50", x.ticker === f.ticker && "bg-ink-800/70")}>
                <td className="px-4 py-2"><span className="font-semibold">{x.ticker}</span><span className="ml-2 text-[12.5px] text-ink-500">{x.name}</span></td>
                <td className="num px-2 text-right">{r.n}</td>
                <td className={clsx("num px-2 text-right", r.accuracy > r.best_naive ? "text-up" : "text-ink-200")}>{pct(r.accuracy, 1, false)}</td>
                <td className="num px-2 text-right text-ink-400">{pct(r.best_naive, 1, false)}</td>
                <td className={clsx("num px-2 text-right", Math.abs(r.z_vs_naive) >= 2 ? tone(r.z_vs_naive) : "text-ink-400")}>{num(r.z_vs_naive, 1)}</td>
                <td className="num px-2 text-right">{r.conf_accuracy == null ? "–" : pct(r.conf_accuracy, 1, false)}</td>
                <td className="num px-2 text-right"><span className={tone(r.signal_sharpe)}>{num(r.signal_sharpe, 2)}</span> <span className="text-ink-500">/ {num(r.buyhold_sharpe, 2)}</span></td>
              </tr>); })}</tbody>
          </table>
        </div>
        <Card title={<span className="flex items-center gap-2">{f.ticker} <span className="text-[13px] font-normal text-ink-400">{f.name}</span>
          {d.accuracy > d.best_naive ? <Pill color="up">beats naive</Pill> : <Pill>no edge over naive</Pill>}</span>}>
          <div className="label mb-1">Cumulative log return: trading the signal long/short vs holding{h === "h1" ? " (open → close only)" : ""}</div>
          <div className="h-[220px]">
            <ResponsiveContainer>
              <LineChart data={d.curve} margin={{ left: -6, right: 8 }}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="d" tickLine={false} axisLine={false} minTickGap={70} />
                <YAxis tickFormatter={(v) => pct(v, 0)} tickLine={false} axisLine={false} />
                <Tooltip {...chartTooltip} formatter={(v: number, n: string) => [pct(v, 1), n]} />
                <ReferenceLine y={0} stroke="var(--color-ink-500)" />
                <Line dataKey="signal" stroke="var(--color-accent)" dot={false} strokeWidth={2} name="model signal" />
                <Line dataKey="buyhold" stroke="var(--color-ink-400)" dot={false} strokeWidth={1.2} strokeDasharray="4 3" name="holding" />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {Object.entries(d.by_year).map(([y, a]) => (
              <span key={y} className={clsx("chip ring-1", a >= d.best_naive ? "bg-up/8 text-up ring-up/20" : "bg-ink-800 text-ink-300 ring-ink-700")}>{y} <b className="num">{pct(a, 0, false)}</b></span>))}
          </div>
          <p className="mt-3 text-[13px] leading-relaxed text-ink-400">Index direction is the hardest case in the literature (Welch & Goyal 2008): funds rise on {pct(d.up_rate, 0, false)} of periods, so "always up" is the bar to clear. Use this page to see whether the model clears it before trading index options on its signal.</p>
        </Card>
      </div>
    </div>
  );
}
