import clsx from "clsx";
import type { ReactNode } from "react";
import { pct, tone } from "../lib/format";

export function Card({ children, className, title, right, pad = true }: {
  children: ReactNode; className?: string; title?: ReactNode; right?: ReactNode; pad?: boolean;
}) {
  return (
    <section className={clsx("card fade-in", className)}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-3 px-5 pt-4 pb-2">
          <h3 className="text-[15px] font-semibold tracking-tight text-ink-100">{title}</h3>
          {right}
        </header>
      )}
      <div className={clsx(pad && "px-5 pb-5", !title && !right && pad && "pt-5")}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, valueClass, hint }: {
  label: string; value: ReactNode; sub?: ReactNode; valueClass?: string; hint?: string;
}) {
  return (
    <div className="card px-4 py-3.5 fade-in" title={hint}>
      <div className="label">{label}</div>
      <div className={clsx("num mt-1.5 text-[23px] font-semibold leading-none tracking-tight text-ink-100", valueClass)}>{value}</div>
      {sub && <div className="mt-1.5 text-[12.5px] leading-snug text-ink-400">{sub}</div>}
    </div>
  );
}

export function Pill({ children, color = "ink", className }: { children: ReactNode; color?: "up" | "down" | "amber" | "accent" | "sky" | "ink"; className?: string }) {
  const c = {
    up: "bg-up/12 text-up ring-1 ring-up/25",
    down: "bg-down/12 text-down ring-1 ring-down/25",
    amber: "bg-amber/12 text-amber ring-1 ring-amber/25",
    accent: "bg-accent/14 text-accent ring-1 ring-accent/30",
    sky: "bg-sky/12 text-sky ring-1 ring-sky/25",
    ink: "bg-ink-800 text-ink-300 ring-1 ring-ink-600",
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

export function PageHeader({ title, sub, right }: { title: ReactNode; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-[26px] font-semibold tracking-tight text-ink-100">{title}</h1>
        {sub && <p className="mt-1 max-w-[80ch] text-[14px] text-ink-400">{sub}</p>}
      </div>
      {right}
    </div>
  );
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { v: T; label: ReactNode }[] }) {
  return (
    <div className="inline-flex rounded-lg border border-ink-600 bg-ink-950 p-0.5">
      {items.map((it) => (
        <button key={it.v} onClick={() => onChange(it.v)}
          className={clsx("rounded-md px-3 py-1.5 text-[13px] font-medium transition",
            value === it.v ? "bg-ink-900 text-ink-100 shadow-sm ring-1 ring-ink-600" : "text-ink-400 hover:text-ink-100")}>
          {it.label}
        </button>
      ))}
    </div>
  );
}

export const chartTooltip = {
  contentStyle: { background: "var(--color-ink-900)", border: "1px solid var(--color-ink-600)", borderRadius: 12, fontSize: 12, boxShadow: "0 10px 30px rgba(0,0,0,.5)" },
  labelStyle: { color: "var(--color-ink-300)", marginBottom: 4 },
  itemStyle: { padding: 0 },
  cursor: { stroke: "var(--color-ink-500)", strokeDasharray: "3 3" },
};
