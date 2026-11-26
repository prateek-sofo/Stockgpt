import clsx from "clsx";
import { Activity, BarChart3, Calculator, CalendarCheck, Database, Globe2, LineChart, List, Menu, Moon, Search, Sun, Sunrise, Telescope } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { STATIC, useApi } from "../lib/api";
import type { Meta } from "../lib/types";

// The rail groups screens by what you are doing, in the order of a trading day.
const NAV: { group?: string; items: { to: string; label: string; icon: typeof Activity }[] }[] = [
  { items: [{ to: "/", label: "Today", icon: Sunrise }] },
  { group: "The day", items: [
    { to: "/news", label: "Live news", icon: Activity },
    { to: "/record", label: "Track record", icon: CalendarCheck },
    { to: "/market", label: "Market", icon: Globe2 },
  ] },
  { group: "Research", items: [
    { to: "/stocks", label: "Stocks", icon: List },
    { to: "/performance", label: "Backtest", icon: BarChart3 },
    { to: "/longterm", label: "Long-term", icon: Telescope },
  ] },
  { group: "Tools", items: [
    { to: "/planner", label: "Planner", icon: Calculator },
    { to: "/data", label: "Data", icon: Database },
  ] },
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
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  const loc = useLocation();
  const { theme, toggle } = useTheme();
  useEffect(() => setOpen(false), [loc.pathname]);
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); document.getElementById("ticker-search")?.focus(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  return (
    <div className="flex min-h-full">
      <aside className={clsx("fixed inset-y-0 left-0 z-40 w-[216px] shrink-0 flex-col border-r border-ink-700 bg-ink-950 px-3 py-3 md:sticky md:top-0 md:flex md:h-screen",
        open ? "flex" : "hidden")}>
        <NavLink to="/" className="mb-3 flex items-center gap-2 px-2 py-1.5">
          <span className="grid h-6 w-6 place-items-center rounded-[6px] bg-ink-100"><LineChart className="h-3.5 w-3.5 text-ink-950" strokeWidth={2.5} /></span>
          <span className="font-[family-name:var(--font-display)] text-[15px] font-semibold tracking-tight text-ink-100">StockGPT</span>
        </NavLink>
        <nav className="flex-1 overflow-y-auto">
          {NAV.map((g, i) => (
            <div key={i} className={clsx(g.group && "mt-4")}>
              {g.group && <div className="t-rail px-2 pb-1.5">{g.group}</div>}
              {g.items.map(({ to, label, icon: Icon }) => (
                <NavLink key={to} to={to} end={to === "/"}
                  className={({ isActive }) => clsx("flex items-center gap-2.5 rounded-[6px] px-2 py-1.5 text-[13.5px] font-medium transition-colors",
                    isActive ? "bg-ink-850 text-ink-100" : "text-ink-200 hover:bg-ink-800 hover:text-ink-100")}>
                  <Icon className="h-4 w-4 text-ink-400" strokeWidth={1.75} />{label}
                </NavLink>))}
            </div>))}
        </nav>
        <div className="border-t border-ink-700 px-2 pt-3">
          {meta && (<>
            <div className="flex items-center gap-2 text-[12.5px] font-medium text-ink-100">
              <span className={clsx("h-1.5 w-1.5 rounded-full", meta.source === "synthetic" ? "bg-amber" : "bg-up")} />
              {meta.source === "synthetic" ? "Demo market" : "Real data"}
            </div>
            <div className="t-small mt-1">Prices through {new Date(meta.data_end + "T12:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" })} · {meta.tickers.toLocaleString()} stocks modelled, {meta.universe} tradable</div>
          </>)}
          {STATIC && <div className="t-small mt-1.5">This is a saved copy. It does not update on its own.</div>}
        </div>
      </aside>
      {open && <button className="fixed inset-0 z-30 bg-black/30 md:hidden" aria-label="Close menu" onClick={() => setOpen(false)} />}

      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-20 flex h-[52px] items-center gap-3 border-b border-ink-700 bg-ink-950/95 px-4 backdrop-blur md:px-8">
          <button className="rounded-[6px] p-1.5 text-ink-300 hover:bg-ink-800 md:hidden" aria-label="Open menu" onClick={() => setOpen(true)}><Menu className="h-4 w-4" /></button>
          <form onSubmit={(e) => { e.preventDefault(); if (q.trim()) nav(`/t/${q.trim().toUpperCase()}`); setQ(""); }}
            className="flex h-[30px] w-full max-w-[300px] items-center gap-2 rounded-[6px] bg-ink-850 px-2.5 focus-within:outline focus-within:outline-2 focus-within:outline-accent">
            <Search className="h-3.5 w-3.5 text-ink-400" />
            <input id="ticker-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Jump to a ticker" aria-label="Jump to a ticker"
              className="w-full bg-transparent text-[13px] text-ink-100 outline-none placeholder:text-ink-400" />
            <kbd className="num hidden text-[11px] text-ink-400 sm:block">⌘K</kbd>
          </form>
          <span className="ml-auto hidden text-[12.5px] text-ink-400 lg:block">Research tool, not investment advice</span>
          <button onClick={toggle} aria-label="Switch light or dark theme" title="Switch theme"
            className="ml-auto rounded-[6px] p-1.5 text-ink-300 hover:bg-ink-800 hover:text-ink-100 lg:ml-0">
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
        </header>
        <main className="mx-auto max-w-[1180px] px-4 py-7 md:px-8">
          <Outlet />
          <footer className="t-small mt-12 border-t border-ink-700 pt-4">
            Options can lose their full premium. Every figure on this site comes from a stored price, filing or headline and carries its date.
          </footer>
        </main>
      </div>
    </div>
  );
}
