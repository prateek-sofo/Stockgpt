import clsx from "clsx";
import { Activity, BarChart3, Database, Gauge, Layers, LineChart, Search, Sunrise, TrendingUp } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useApi } from "../lib/api";
import type { Meta } from "../lib/types";

const NAV = [
  { to: "/", label: "Morning Brief", icon: Sunrise },
  { to: "/backtest", label: "Backtest Lab", icon: BarChart3 },
  { to: "/planner", label: "Growth Planner", icon: Gauge },
  { to: "/universe", label: "Universe", icon: Layers },
  { to: "/longterm", label: "Long-term", icon: TrendingUp },
  { to: "/data", label: "Data & Models", icon: Database },
];

export default function Layout() {
  const { data: meta } = useApi<Meta>("meta");
  const [q, setQ] = useState("");
  const nav = useNavigate();
  return (
    <div className="flex min-h-full">
      <aside className="sticky top-0 hidden h-screen w-[232px] shrink-0 flex-col border-r border-ink-800 bg-ink-950/70 px-3 py-5 backdrop-blur md:flex">
        <div className="mb-7 flex items-center gap-2.5 px-2">
          <div className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-accent to-up shadow-[0_0_24px_rgba(139,123,255,.35)]">
            <LineChart className="h-5 w-5 text-ink-950" strokeWidth={2.5} />
          </div>
          <div>
            <div className="text-[15px] font-semibold tracking-tight">StockGPT</div>
            <div className="text-[10.5px] text-ink-400">news → edge → trade</div>
          </div>
        </div>
        <nav className="space-y-0.5">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === "/"}
              className={({ isActive }) => clsx("group flex items-center gap-3 rounded-xl px-3 py-2 text-[13px] font-medium transition",
                isActive ? "bg-ink-800 text-ink-100 shadow-[inset_0_0_0_1px_#283044]" : "text-ink-400 hover:bg-ink-900 hover:text-ink-200")}>
              {({ isActive }) => (<><Icon className={clsx("h-4 w-4", isActive ? "text-accent" : "text-ink-500 group-hover:text-ink-300")} />{label}</>)}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto space-y-3 px-2">
          {meta && (
            <div className="rounded-xl border border-ink-800 bg-ink-900/60 p-3 text-[11px] text-ink-400">
              <div className="mb-1.5 flex items-center gap-1.5">
                <Activity className="h-3.5 w-3.5 text-up" />
                <span className="font-medium text-ink-200">{meta.source === "synthetic" ? "Demo market" : "Live data"}</span>
              </div>
              <div className="num">{meta.tickers} tickers · {meta.universe} liquid</div>
              <div className="num">{(meta.news / 1000).toFixed(0)}K news · {meta.features} features</div>
              <div className="num">thru {meta.data_end}</div>
            </div>
          )}
          <div className="text-[10px] leading-snug text-ink-500">Research tool. Not investment advice. Options can lose 100% of premium.</div>
        </div>
      </aside>
      <main className="min-w-0 flex-1">
        <div className="sticky top-0 z-20 flex items-center gap-3 border-b border-ink-800/80 bg-ink-950/75 px-6 py-3 backdrop-blur-md">
          <form onSubmit={(e) => { e.preventDefault(); if (q.trim()) nav(`/t/${q.trim().toUpperCase()}`); setQ(""); }}
            className="flex w-full max-w-md items-center gap-2 rounded-xl border border-ink-700 bg-ink-900 px-3 py-1.5 focus-within:border-accent/60">
            <Search className="h-4 w-4 text-ink-500" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Jump to ticker…"
              className="w-full bg-transparent text-[13px] outline-none placeholder:text-ink-500" />
            <kbd className="num rounded border border-ink-700 px-1.5 text-[10px] text-ink-500">↵</kbd>
          </form>
          {meta?.source === "synthetic" && (
            <div className="ml-auto hidden items-center gap-2 rounded-full border border-amber/30 bg-amber/10 px-3 py-1 text-[11.5px] text-amber lg:flex">
              <span className="h-1.5 w-1.5 rounded-full bg-amber" /> Synthetic demo data. Drop your scraped tables in data/raw to go live.
            </div>
          )}
        </div>
        <div className="mx-auto max-w-[1440px] px-6 py-7">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
