import clsx from "clsx";
import { ArrowDown, ArrowUp } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ErrorBox, Loading, Pill, Tabs, ToneDot } from "../components/ui";
import { useApi } from "../lib/api";
import { compact, num, pct, tone } from "../lib/format";
import type { AllData } from "../lib/types";

type R = { ticker: string; name: string; exchange: string; sector: string; price: number | null; d1: number | null; m1: number | null; y1: number | null;
  dollar_volume: number; filings: number; news: number; line: string; tone: string; action: string; accuracy: number | null };
const NUMS: { k: keyof R; label: string; hint: string; fmt: (r: R) => React.ReactNode; cls?: (r: R) => string }[] = [
  { k: "price", label: "Price", hint: "last close", fmt: (r) => (r.price == null ? "–" : num(r.price, 2)) },
  { k: "d1", label: "Day", hint: "last session", fmt: (r) => pct(r.d1, 1), cls: (r) => tone(r.d1) },
  { k: "m1", label: "Month", hint: "past 21 sessions", fmt: (r) => pct(r.m1, 0), cls: (r) => tone(r.m1) },
  { k: "y1", label: "Year", hint: "past 12 months", fmt: (r) => pct(r.y1, 0), cls: (r) => tone(r.y1) },
  { k: "dollar_volume", label: "Traded / day", hint: "average dollars traded per day, past month", fmt: (r) => (r.dollar_volume ? compact(r.dollar_volume, "$") : "–") },
  { k: "filings", label: "Filings", hint: "notable SEC filings in the past 45 days", fmt: (r) => r.filings || "–" },
  { k: "news", label: "News", hint: "news stories in the past 10 days", fmt: (r) => r.news || "–" },
];

export default function AllTickers() {
  const { data, error } = useApi<AllData>("all");
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [ex, setEx] = useState("All");
  const [only, setOnly] = useState<"all" | "active" | "model">("all");
  const [sort, setSort] = useState<{ k: keyof R; d: 1 | -1 }>({ k: "dollar_volume", d: -1 });
  const all = useMemo(() => (data ? data.rows.map((r) => Object.fromEntries(data.cols.map((c, i) => [c, r[i]])) as unknown as R) : []), [data]);
  const rows = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return all
      .filter((r) => (ex === "All" || r.exchange === ex) && (only === "all" || (only === "active" ? r.filings + r.news > 0 : r.accuracy != null))
        && (!ql || r.ticker.toLowerCase().startsWith(ql) || r.name.toLowerCase().includes(ql)))
      .sort((a, b) => (((a[sort.k] as number) ?? -1e18) > ((b[sort.k] as number) ?? -1e18) ? sort.d : -sort.d));
  }, [all, q, ex, only, sort]);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const exchanges = ["All", ...[...new Set(all.map((r) => r.exchange))].sort()];
  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 border-b border-ink-800 px-4 py-3">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search ticker or company…" autoFocus
          className="w-64 rounded-xl border border-ink-700 bg-ink-900 px-3 py-1.5 text-[13.5px] outline-none focus:border-accent/60" />
        <select value={ex} onChange={(e) => setEx(e.target.value)} aria-label="Exchange" className="rounded-xl border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-[13.5px] text-ink-100">
          {exchanges.map((e) => <option key={e}>{e}</option>)}
        </select>
        <Tabs value={only} onChange={setOnly} items={[{ v: "all", label: "Everything" }, { v: "active", label: "With news or filings" }, { v: "model", label: "Model follows" }]} />
        <span className="ml-auto text-[13px] text-ink-400">{rows.length.toLocaleString()} of {all.length.toLocaleString()} tickers · prices through {data.as_of}</span>
      </div>
      <div className="max-h-[720px] overflow-auto">
        <table className="w-full text-[13.5px]">
          <thead className="sticky top-0 z-10 bg-ink-900"><tr className="text-left text-ink-500">
            <th className="px-4 py-2.5 font-medium">Stock</th>
            {NUMS.map((c) => (
              <th key={c.k} title={c.hint} onClick={() => setSort((p) => ({ k: c.k, d: p.k === c.k ? (-p.d as 1 | -1) : -1 }))}
                className="cursor-pointer whitespace-nowrap px-2 text-right font-medium hover:text-ink-200">
                <span className="inline-flex items-center gap-1">{c.label}{sort.k === c.k && (sort.d > 0 ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}</span>
              </th>))}
            <th className="px-3 font-medium">What is happening</th>
            <th className="px-4 text-right font-medium" title="today's model idea, and how often the model has been right on this stock">Model</th>
          </tr></thead>
          <tbody>{rows.slice(0, 300).map((r) => (
            <tr key={r.ticker} onClick={() => nav(`/t/${r.ticker}`)} className="cursor-pointer border-t border-ink-800/70 transition hover:bg-ink-800/50">
              <td className="px-4 py-2"><span className="font-semibold">{r.ticker}</span>
                <span className="ml-2 inline-block max-w-[190px] truncate align-bottom text-[12.5px] text-ink-500">{r.name}</span>
                <span className="ml-2 text-[11.5px] text-ink-500">{r.exchange}</span></td>
              {NUMS.map((c) => <td key={c.k} className={clsx("num px-2 text-right", c.cls?.(r))}>{c.fmt(r)}</td>)}
              <td className="max-w-[360px] px-3"><span className="flex items-start gap-2">{r.tone && <ToneDot t={r.tone} />}<span className="truncate text-[13px] text-ink-300" title={r.line}>{r.line}</span></span></td>
              <td className="whitespace-nowrap px-4 text-right">
                {r.action ? <Pill color={r.action === "BUY" ? "up" : "down"}>{r.action === "BUY" ? "Buy idea" : "Short idea"}</Pill>
                  : r.accuracy != null ? <span className="num text-[12.5px] text-ink-400">{pct(r.accuracy, 0, false)} right</span> : <span className="text-[12.5px] text-ink-500">not modelled</span>}
              </td>
            </tr>))}</tbody>
        </table>
        {rows.length > 300 && <div className="border-t border-ink-800 px-4 py-3 text-center text-[13px] text-ink-400">Showing the first 300. Search or filter to narrow down.</div>}
      </div>
    </div>
  );
}
