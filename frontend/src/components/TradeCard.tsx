import clsx from "clsx";
import { ArrowDownRight, ArrowUpRight, CalendarClock, CheckCircle2, CircleDashed, XCircle } from "lucide-react";
import { Link } from "react-router-dom";
import { compact, pct, TRAIT_LABEL, usd } from "../lib/format";
import type { Ticket } from "../lib/types";
import { Bar, Pill, ProbRing, SentDot } from "./ui";

export default function TradeCard({ t, rank }: { t: Ticket; rank: number }) {
  const up = t.direction === "UP";
  const maxC = Math.max(...t.why.map((w) => Math.abs(w.contribution)), 1e-6);
  const traits = Object.entries(t.ticker_model).filter(([k]) => k !== "eps_surprise" || t.earnings_today);
  return (
    <article className={clsx("card card-hover group relative overflow-hidden p-5 fade-in",
      t.qualifies && "ring-1 ring-accent/35")}>
      <div className={clsx("pointer-events-none absolute -right-16 -top-16 h-40 w-40 rounded-full blur-3xl opacity-25",
        up ? "bg-up" : "bg-down")} />
      <div className="flex items-start gap-4">
        <ProbRing p={t.prob} dir={t.direction} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="num text-[11px] text-ink-500">#{rank}</span>
            <Link to={`/t/${t.ticker}`} className="text-[19px] font-semibold tracking-tight hover:text-accent">{t.ticker}</Link>
            <span className={clsx("flex items-center gap-0.5 text-[12px] font-semibold", up ? "text-up" : "text-down")}>
              {up ? <ArrowUpRight className="h-4 w-4" /> : <ArrowDownRight className="h-4 w-4" />}{t.direction}
            </span>
            <div className="ml-auto flex items-center gap-1.5">
              {t.earnings_today && <Pill color="amber"><CalendarClock className="h-3 w-3" />Earnings</Pill>}
              {t.qualifies ? <Pill color="accent">Qualifies</Pill> : <Pill>Watchlist</Pill>}
            </div>
          </div>
          <div className="truncate text-[12.5px] text-ink-400">{t.name} · {t.sector}</div>
        </div>
      </div>

      {t.kind === "option" ? (
        <div className="mt-4 rounded-xl border border-ink-700/80 bg-ink-950/60 p-3">
          <div className="flex items-center justify-between">
            <span className="num text-[13.5px] font-semibold text-ink-100">{t.contract}</span>
            <span className="num text-[12px] text-ink-400">@ {usd(t.premium, 2)}</span>
          </div>
          <div className="mt-2.5 grid grid-cols-4 gap-2 text-[11px]">
            <KV k="Contracts" v={t.contracts} />
            <KV k="Max loss" v={usd(t.max_loss)} />
            <KV k="EV (calibrated)" v={pct(t.ev)} cls={t.ev! > 0 ? "text-up" : "text-down"} />
            <KV k="P(profit)" v={pct(t.p_profit, 0, false)} />
          </div>
        </div>
      ) : (
        <div className="mt-4 rounded-xl border border-ink-700/80 bg-ink-950/60 p-3">
          <div className="flex items-center justify-between">
            <span className="num text-[13.5px] font-semibold text-ink-100">{up ? "BUY" : "SHORT"} {t.shares} sh</span>
            <span className="num text-[12px] text-ink-400">ref {usd(t.ref_price, 2)}</span>
          </div>
          <div className="mt-2.5 grid grid-cols-4 gap-2 text-[11px]">
            <KV k="Notional" v={compact(t.notional, "$")} />
            <KV k="Exp. return" v={pct(t.exp_ret, 2)} cls={t.exp_ret! > 0 ? "text-up" : "text-down"} />
            <KV k="Exp. move" v={pct(t.exp_move_pct, 1, false)} />
            <KV k="Premarket" v={pct(t.premarket_gap, 2)} cls={t.premarket_gap > 0 ? "text-up" : "text-down"} />
          </div>
        </div>
      )}

      <div className="mt-4">
        <div className="label mb-2">Why the model likes it</div>
        <div className="space-y-1.5">
          {t.why.slice(0, 4).map((w) => (
            <div key={w.key} className="grid grid-cols-[1fr_88px] items-center gap-3 text-[12px]">
              <span className="truncate text-ink-300">{w.factor}</span>
              <Bar v={w.contribution} max={maxC} />
            </div>
          ))}
        </div>
      </div>

      {t.headlines.length > 0 && (
        <div className="mt-4 space-y-1.5">
          <div className="label">Catalysts</div>
          {t.headlines.slice(0, 3).map((h, i) => (
            <div key={i} className="flex items-start gap-2 text-[12.5px] leading-snug">
              <SentDot s={h.sentiment} />
              <span className="line-clamp-2 text-ink-200">{h.headline}</span>
              <span className="num ml-auto shrink-0 text-[10.5px] text-ink-500">{h.time}</span>
            </div>
          ))}
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-1.5">
        {traits.map(([k, m]) => {
          const L = TRAIT_LABEL[k];
          const rel = m.effective - m.market_beta;
          return (
            <span key={k} title={`${L?.name}: this ticker ${m.effective >= 0 ? L?.pos : L?.neg}. Evidence ${m.evidence.toFixed(0)}.`}
              className="chip bg-ink-800 text-ink-300 ring-1 ring-ink-700">
              {L?.name ?? k}
              <b className={clsx("num", rel > 0 ? "text-up" : "text-down")}>{m.effective >= 0 ? "+" : ""}{m.effective.toFixed(3)}</b>
            </span>
          );
        })}
      </div>

      <div className="mt-4 flex items-center justify-between border-t border-ink-800 pt-3 text-[11.5px]">
        <span className="text-ink-400">{t.plan}</span>
      </div>
      {t.outcome && (
        <div className={clsx("mt-3 flex items-center gap-2 rounded-lg px-3 py-2 text-[12px]",
          t.outcome.correct ? "bg-up/8 text-up" : "bg-down/8 text-down")}>
          {t.outcome.correct ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
          <span>Actual session: stock {pct(t.outcome.stock_move, 2)}</span>
          {t.outcome.option_ret != null && <span className="num ml-auto">option {pct(t.outcome.option_ret, 0)}</span>}
          {t.outcome.stock_ret != null && <span className="num ml-auto">trade {pct(t.outcome.stock_ret, 2)}</span>}
        </div>
      )}
      {!t.outcome && <div className="mt-3 flex items-center gap-2 text-[11.5px] text-ink-500"><CircleDashed className="h-3.5 w-3.5" />Pending session</div>}
    </article>
  );
}

function KV({ k, v, cls }: { k: string; v: React.ReactNode; cls?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-ink-500">{k}</div>
      <div className={clsx("num mt-0.5 text-[13px] font-medium text-ink-100", cls)}>{v}</div>
    </div>
  );
}
