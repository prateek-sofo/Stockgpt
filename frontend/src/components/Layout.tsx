import clsx from "clsx";
import { LineChart, Moon, Search, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { STATIC, useApi } from "../lib/api";
import type { Meta } from "../lib/types";

const NAV = [
  { to: "/", label: "Today" },
  { to: "/record", label: "Track record" },
  { to: "/market", label: "Market" },
  { to: "/stocks", label: "Stocks" },
  { to: "/performance", label: "Performance" },
  { to: "/planner", label: "Planner" },
  { to: "/longterm", label: "Long-term" },
  { to: "/data", label: "Data" },
];

function useTheme() {
  const system = () => (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const [theme, setTheme] = useState<string>(() => document.documentElement.getAttribute("data-theme") ?? system());
  useEffect(() => {
    // the host page may set data-theme itself; follow it
    const obs = new MutationObserver(() => {
      const t = document.documentElement.getAttribute("data-theme");
      if (t) setTheme(t);
    });
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    setTheme(next);
  };
  return { theme, toggle };
}

export default function Layout() {
  const { data: meta } = useApi<Meta>("meta");
  const [q, setQ] = useState("");
  const nav = useNavigate();
  const { theme, toggle } = useTheme();
  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-30 border-b border-ink-700 bg-ink-900/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1320px] flex-wrap items-center gap-x-6 gap-y-2 px-5 py-2.5">
          <NavLink to="/" className="flex items-center gap-2">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent"><LineChart className="h-[18px] w-[18px] text-white" strokeWidth={2.5} /></span>
            <span className="text-[16px] font-semibold tracking-tight text-ink-100">StockGPT</span>
          </NavLink>
          <nav className="flex flex-wrap items-center gap-1">
            {NAV.map(({ to, label }) => (
              <NavLink key={to} to={to} end={to === "/"}
                className={({ isActive }) => clsx("rounded-lg px-3 py-1.5 text-[14px] font-medium transition",
                  isActive ? "bg-ink-800 text-ink-100" : "text-ink-400 hover:text-ink-100")}>
                {label}
              </NavLink>
            ))}
          </nav>
          <form onSubmit={(e) => { e.preventDefault(); if (q.trim()) nav(`/t/${q.trim().toUpperCase()}`); setQ(""); }}
            className="ml-auto flex w-56 items-center gap-2 rounded-lg border border-ink-600 bg-ink-950 px-2.5 py-1.5 focus-within:border-accent">
            <Search className="h-4 w-4 text-ink-400" />
            <input id="ticker-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ticker, then Enter" aria-label="Go to ticker"
              className="w-full bg-transparent text-[14px] text-ink-100 outline-none placeholder:text-ink-500" />
          </form>
          <button onClick={toggle} aria-label="Switch light or dark theme" title="Switch theme"
            className="rounded-lg border border-ink-600 p-1.5 text-ink-300 hover:text-ink-100">
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-[1320px] px-5 py-6">
        <Outlet />
        <footer className="mt-10 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-ink-700 pt-4 text-[12.5px] text-ink-400">
          {meta && <span className="num">{meta.source === "synthetic" ? "Demo market" : "Real data"} through {meta.data_end} · {meta.tickers.toLocaleString()} stocks · {meta.universe} tradable</span>}
          {STATIC && <span>Snapshot: results do not update here.</span>}
          <span className="ml-auto">Research tool, not investment advice. Options can lose their full premium.</span>
        </footer>
      </main>
    </div>
  );
}
