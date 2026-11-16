import clsx from "clsx";
import { ArrowDown, ArrowUp } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ErrorBox, Loading, PageHeader, Pill, Spark, Tabs } from "../components/ui";
import { useApi } from "../lib/api";
import { compact, num, pct, tone } from "../lib/format";
import type { UniverseRow } from "../lib/types";

type Col = { k: keyof UniverseRow; label: string; fmt: (r: UniverseRow) => React.ReactNode; cls?: (r: UniverseRow) => string; hint?: string };
const COLS: Col[] = [
  { k: "price", label: "Price", fmt: (r) => num(r.price, 2) },
  { k: "chg", label: "1D", fmt: (r) => pct(r.chg, 2), cls: (r) => tone(r.chg) },
  { k: "adv", label: "$ Volume", fmt: (r) => compact(r.adv, "$"), hint: "20d average dollar volume" },
  { k: "opt_vol", label: "Opt vol", fmt: (r) => compact(r.opt_vol), hint: "20d average contracts/day" },
  { k: "spread", label: "Spread", fmt: (r) => pct(r.spread, 1, false), hint: "ATM bid-ask as % of mid" },
  { k: "rv20", label: "RV20", fmt: (r) => pct(r.rv20, 0, false) },
  { k: "iv30", label: "IV30", fmt: (r) => pct(r.iv30, 0, false) },
  { k: "news_drift", label: "News drift", fmt: (r) => num(r.news_drift, 3), cls: (r) => tone(r.news_drift), hint: "This ticker's own effective coefficient on overnight news" },
  { k: "gap_follow", label: "Gap habit", fmt: (r) => num(r.gap_follow, 3), cls: (r) => tone(r.gap_follow), hint: "+ extends gaps, − fades gaps" },
  { k: "ic", label: "Model IC", fmt: (r) => num(r.ic, 3), cls: (r) => tone(r.ic), hint: "Out-of-sample rank correlation for this ticker" },
  { k: "days_to_earn", label: "Earnings", fmt: (r) => (r.days_to_earn < 900 ? `${r.days_to_earn}d` : "–"), cls: (r) => (r.days_to_earn <= 5 ? "text-amber" : "") },
];

export default function Universe() {
  const { data, error } = useApi<UniverseRow[]>("universe");
  const [q, setQ] = useState("");
  const [only, setOnly] = useState<"liquid" | "all">("liquid");
  const [sort, setSort] = useState<{ k: keyof UniverseRow; d: 1 | -1 }>({ k: "adv", d: -1 });
  const [sector, setSector] = useState("All");
  const nav = useNavigate();
  const rows = useMemo(() => {
    if (!data) return [];
    const ql = q.toLowerCase();
    return data
      .filter((r) => (only === "all" || r.liquid) && (sector === "All" || r.sector === sector) && (!ql || r.ticker.toLowerCase().includes(ql) || r.name.toLowerCase().includes(ql)))
      .sort((a, b) => ((a[sort.k] as number) ?? -1e18) > ((b[sort.k] as number) ?? -1e18) ? sort.d : -sort.d);
  }, [data, q, only, sort, sector]);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const sectors = ["All", ...new Set(data.map((r) => r.sector))].sort();
  return (
    <div>
      <PageHeader title="Universe" sub={`${data.filter((r) => r.liquid).length} of ${data.length} names pass the liquidity gate (price, $ volume, options volume, spread). Each has its own behavioural model.`}
        right={<div className="flex flex-wrap items-center gap-2">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter…" className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-1.5 text-[13px] outline-none focus:border-accent/60" />
          <select value={sector} onChange={(e) => setSector(e.target.value)} className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-1.5 text-[13px] outline-none">
            {sectors.map((s) => <option key={s}>{s}</option>)}
          </select>
          <Tabs value={only} onChange={setOnly} items={[{ v: "liquid", label: "Liquid" }, { v: "all", label: "All" }]} />
        </div>} />
      <div className="card overflow-hidden">
        <div className="max-h-[calc(100vh-220px)] overflow-auto">
          <table className="w-full text-[12.5px]">
            <thead className="sticky top-0 z-10 bg-ink-900/95 backdrop-blur">
              <tr className="text-left text-ink-500">
                <th className="px-4 py-3 font-medium">Ticker</th>
                <th className="font-medium">60d</th>
                {COLS.map((c) => (
                  <th key={c.k} title={c.hint} className="cursor-pointer whitespace-nowrap px-2 text-right font-medium hover:text-ink-200"
                    onClick={() => setSort((s) => ({ k: c.k, d: s.k === c.k ? (-s.d as 1 | -1) : -1 }))}>
                    <span className="inline-flex items-center gap-1">{c.label}{sort.k === c.k && (sort.d > 0 ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}</span>
                  </th>))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.ticker} onClick={() => nav(`/t/${r.ticker}`)} className="cursor-pointer border-t border-ink-800/70 transition hover:bg-ink-800/50">
                  <td className="px-4 py-2">
                    <div className="flex items-center gap-2"><span className="font-semibold">{r.ticker}</span>{!r.liquid && <Pill>illiquid</Pill>}</div>
                    <div className="max-w-[220px] truncate text-[11px] text-ink-500">{r.name} · {r.sector}</div>
                  </td>
                  <td><Spark data={r.spark} w={80} h={24} /></td>
                  {COLS.map((c) => <td key={c.k} className={clsx("num px-2 text-right", c.cls?.(r))}>{c.fmt(r)}</td>)}
                </tr>))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
