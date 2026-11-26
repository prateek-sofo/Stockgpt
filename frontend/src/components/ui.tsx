import clsx from "clsx";
import type { ReactNode } from "react";
import { pct, tone } from "../lib/format";

/** A section of a page. By default it is a heading over a hairline with its content below, no box:
 *  on a white page the rule is enough. `pad={false}` is for a table or list that is one object and
 *  scrolls on its own; that one gets a surface. */
export function Card({ children, className, title, right, pad = true }: {
  children: ReactNode; className?: string; title?: ReactNode; right?: ReactNode; pad?: boolean;
}) {
  if (!pad) {
    return (
      <section className={clsx("card fade-in overflow-hidden", className)}>
        {(title || right) && (
          <header className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-700 px-5 py-2.5">
            <h3 className="t-head">{title}</h3>
            {right}
          </header>
        )}
        {children}
      </section>
    );
  }
  return (
    <section className={clsx("fade-in", className)}>
      {(title || right) && (
        <header className="mb-3 flex flex-wrap items-center justify-between gap-3 border-b border-ink-700 pb-2">
          <h3 className="t-head">{title}</h3>
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, sub, valueClass, hint }: {
  label: string; value: ReactNode; sub?: ReactNode; valueClass?: string; hint?: string;
}) {
  return (
    <div className="border-t border-ink-700 pt-3 fade-in" title={hint}>
      <div className="t-label">{label}</div>
      <div className={clsx("mt-1.5 font-[family-name:var(--font-display)] text-[22px] font-semibold leading-none tracking-tight text-ink-100 [font-variant-numeric:tabular-nums]", valueClass)}>{value}</div>
      {sub && <div className="t-small mt-1.5">{sub}</div>}
    </div>
  );
}

export function Pill({ children, color = "ink", className }: { children: ReactNode; color?: "up" | "down" | "amber" | "accent" | "sky" | "ink"; className?: string }) {
  const c = {
    up: "bg-up/10 text-up",
    down: "bg-down/10 text-down",
    amber: "bg-ink-850 text-ink-200 before:mr-0.5 before:inline-block before:h-1.5 before:w-1.5 before:rounded-full before:bg-amber before:content-['']",
    accent: "bg-wash text-accent",
    sky: "bg-sky/10 text-sky",
    ink: "bg-ink-850 text-ink-300",
  }[color];
  return <span className={clsx("chip", c, className)}>{children}</span>;
}

export function Delta({ v, d = 1, className }: { v: number | null | undefined; d?: number; className?: string }) {
  return <span className={clsx("num", tone(v), className)}>{pct(v, d)}</span>;
}

export function SentDot({ s }: { s: number }) {
  const c = s > 0.2 ? "bg-up" : s < -0.2 ? "bg-down" : "bg-ink-400";
  const glow = "";
  return <span className={clsx("inline-block h-2 w-2 shrink-0 rounded-full", c, glow)} />;
}

export function ProbRing({ p, dir, size = 64 }: { p: number; dir: "UP" | "DOWN"; size?: number }) {
  const r = size / 2 - 5;
  const C = 2 * Math.PI * r;
  // map 50%..65% onto the ring: edges in this business are small
  const frac = Math.max(0.02, Math.min(1, (p - 0.5) / 0.15));
  const col = dir === "UP" ? "var(--color-up)" : "var(--color-down)";
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} stroke="var(--color-ink-700)" strokeWidth={5} fill="none" />
        <circle cx={size / 2} cy={size / 2} r={r} stroke={col} strokeWidth={5} fill="none" strokeLinecap="round"
          strokeDasharray={`${C * frac} ${C}`} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="num text-[13px] font-semibold">{(p * 100).toFixed(1)}</span>
        <span className="text-[9px] text-ink-400">{dir === "UP" ? "beats mkt" : "lags mkt"}</span>
      </div>
    </div>
  );
}

export function Spark({ data, w = 96, h = 28, color }: { data: number[]; w?: number; h?: number; color?: string }) {
  if (!data?.length) return null;
  const lo = Math.min(...data), hi = Math.max(...data);
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - lo) / (hi - lo || 1)) * (h - 2) - 1}`).join(" ");
  const c = color ?? (data[data.length - 1] >= data[0] ? "var(--color-up)" : "var(--color-down)");
  return (
    <svg width={w} height={h} className="overflow-visible">
      <polyline points={pts} fill="none" stroke={c} strokeWidth={1.4} strokeLinejoin="round" />
    </svg>
  );
}

export function Bar({ v, max, pos = "var(--color-up)", neg = "var(--color-down)" }: { v: number; max: number; pos?: string; neg?: string }) {
  const w = Math.min(50, (Math.abs(v) / (max || 1)) * 50);
  return (
    <div className="relative h-1.5 w-full rounded-full bg-ink-700/70">
      <div className="absolute top-0 h-full w-px bg-ink-500" style={{ left: "50%" }} />
      <div className="absolute top-0 h-full rounded-full" style={{
        left: v >= 0 ? "50%" : `${50 - w}%`, width: `${w}%`, background: v >= 0 ? pos : neg,
      }} />
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="space-y-4">
      <div className="h-8 w-64 rounded-lg shimmer" />
      <div className="grid grid-cols-4 gap-4">{[0, 1, 2, 3].map((i) => <div key={i} className="h-24 rounded-2xl shimmer" />)}</div>
      <div className="h-80 rounded-2xl shimmer" />
      <span className="sr-only">{label}</span>
    </div>
  );
}

export function ErrorBox({ error }: { error: string }) {
  return (
    <div className="card border-down/40 p-6 text-sm">
      <div className="font-semibold text-down">Could not load data</div>
      <div className="mt-1 text-ink-300">{error}</div>
      <div className="mt-3 text-ink-400">Build the artifacts with <code className="num text-ink-200">python -m stockgpt.pipeline</code> and start the API.</div>
    </div>
  );
}

/** The first thing to read on a screen: one sentence in the display size, and under it the
 *  quieter line that qualifies it. */
export function PageHeader({ title, sub, right }: { title: ReactNode; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="t-display">{title}</h1>
        {sub && <div className="t-body mt-1.5 max-w-[78ch] text-ink-400">{sub}</div>}
      </div>
      {right}
    </div>
  );
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { v: T; label: ReactNode }[] }) {
  return (
    <div className="inline-flex rounded-[6px] border border-ink-700 bg-ink-900 p-0.5">
      {items.map((it) => (
        <button key={it.v} onClick={() => onChange(it.v)}
          className={clsx("rounded-[4px] px-2.5 py-1 text-[12.5px] font-medium transition-colors",
            value === it.v ? "bg-ink-850 text-ink-100" : "text-ink-400 hover:text-ink-100")}>
          {it.label}
        </button>
      ))}
    </div>
  );
}

export const chartTooltip = {
  contentStyle: { background: "var(--color-ink-900)", border: "1px solid var(--color-ink-600)", borderRadius: 6, fontSize: 12, boxShadow: "0 14px 34px -22px rgba(14,17,22,.35)" },
  labelStyle: { color: "var(--color-ink-300)", marginBottom: 4 },
  itemStyle: { padding: 0 },
  cursor: { stroke: "var(--color-ink-500)", strokeDasharray: "3 3" },
};

const TONE: Record<string, { dot: string; label: string; pill: "up" | "down" | "amber" | "ink" }> = {
  good: { dot: "bg-up", label: "Usually good", pill: "up" }, bad: { dot: "bg-down", label: "Usually bad", pill: "down" },
  watch: { dot: "bg-amber", label: "Worth a look", pill: "amber" }, info: { dot: "bg-ink-400", label: "Routine", pill: "ink" },
};
export function ToneDot({ t }: { t: string }) {
  return <span className={clsx("mt-[7px] inline-block h-2 w-2 shrink-0 rounded-full", (TONE[t] ?? TONE.info).dot)} />;
}
export function TonePill({ t }: { t: string }) {
  const x = TONE[t] ?? TONE.info;
  return <Pill color={x.pill}>{x.label}</Pill>;
}
