import { useMemo, useRef, useState } from "react";
import type { TickerProfile } from "../lib/types";

type P = TickerProfile["prices"][number];

/** Candlestick chart with overnight-news sentiment bars and earnings markers. Pure SVG. */
export default function CandleChart({ data, height = 360 }: { data: P[]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const W = 1000;
  const priceH = height * 0.74, sentH = height * 0.18, gap = height * 0.08;
  const { lo, hi, smax } = useMemo(() => {
    const lo = Math.min(...data.map((d) => d.l)), hi = Math.max(...data.map((d) => d.h));
    const smax = Math.max(0.2, ...data.map((d) => Math.abs(d.s)));
    return { lo, hi, smax };
  }, [data]);
  if (!data.length) return null;
  const n = data.length, bw = W / n;
  const y = (v: number) => 8 + (1 - (v - lo) / (hi - lo || 1)) * (priceH - 16);
  const sy0 = priceH + gap + sentH / 2;
  const h = hover != null ? data[hover] : data[n - 1];
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => lo + f * (hi - lo));
  return (
    <div ref={ref} className="relative select-none">
      <div className="pointer-events-none absolute left-2 top-1 z-10 flex flex-wrap gap-x-4 gap-y-1 text-[11.5px]">
        <span className="num text-ink-300">{h.d}</span>
        <span className="num">O <b className="font-medium text-ink-100">{h.o.toFixed(2)}</b></span>
        <span className="num">H <b className="font-medium text-ink-100">{h.h.toFixed(2)}</b></span>
        <span className="num">L <b className="font-medium text-ink-100">{h.l.toFixed(2)}</b></span>
        <span className="num">C <b className={h.c >= h.o ? "font-medium text-up" : "font-medium text-down"}>{h.c.toFixed(2)}</b></span>
        {h.n > 0 && <span className="num text-accent">{h.n} overnight news · sentiment {h.s > 0 ? "+" : ""}{h.s.toFixed(2)}</span>}
        {h.e === 1 && <span className="text-amber">Earnings reaction</span>}
      </div>
      <svg viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none" className="h-auto w-full" style={{ height }}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          setHover(Math.max(0, Math.min(n - 1, Math.floor(((e.clientX - r.left) / r.width) * n))));
        }}
        onMouseLeave={() => setHover(null)}>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={0} x2={W} y1={y(t)} y2={y(t)} stroke="var(--color-ink-800)" />
            <text x={W - 4} y={y(t) - 3} textAnchor="end" className="num" fontSize={10} fill="var(--color-ink-500)">{t.toFixed(2)}</text>
          </g>
        ))}
        {data.map((d, i) => {
          const x = i * bw + bw / 2, up = d.c >= d.o, col = up ? "var(--color-up)" : "var(--color-down)";
          return (
            <g key={d.d}>
              {d.e === 1 && <rect x={i * bw} y={0} width={bw} height={priceH} fill="var(--color-amber)" opacity={0.08} />}
              <line x1={x} x2={x} y1={y(d.h)} y2={y(d.l)} stroke={col} strokeWidth={1} opacity={0.8} />
              <rect x={i * bw + bw * 0.18} width={bw * 0.64} y={y(Math.max(d.o, d.c))}
                height={Math.max(1, Math.abs(y(d.o) - y(d.c)))} fill={col} opacity={up ? 0.85 : 0.9} rx={0.5} />
              {d.n > 0 && (
                <rect x={i * bw + bw * 0.15} width={bw * 0.7} y={d.s >= 0 ? sy0 - (d.s / smax) * (sentH / 2) : sy0}
                  height={Math.max(1, (Math.abs(d.s) / smax) * (sentH / 2))} fill={d.s >= 0 ? "var(--color-up)" : "var(--color-down)"} opacity={0.55} />
              )}
              {d.e === 1 && <circle cx={x} cy={priceH - 6} r={3} fill="var(--color-amber)" />}
            </g>
          );
        })}
        <line x1={0} x2={W} y1={sy0} y2={sy0} stroke="var(--color-ink-600)" />
        <text x={4} y={priceH + gap - 2} fontSize={10} fill="var(--color-ink-400)">overnight news sentiment</text>
        {hover != null && <line x1={hover * bw + bw / 2} x2={hover * bw + bw / 2} y1={0} y2={height} stroke="var(--color-ink-400)" strokeDasharray="3 3" />}
      </svg>
    </div>
  );
}
