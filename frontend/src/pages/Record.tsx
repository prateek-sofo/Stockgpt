import clsx from "clsx";
import { Check, ChevronDown, ChevronRight, Lock, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, ErrorBox, Loading, PageHeader, Pill, Stat } from "../components/ui";
import { useApi } from "../lib/api";
import { pct, tone, usd } from "../lib/format";
import type { JournalDay, JournalIndex } from "../lib/types";

const longDay = (d: string) => new Date(d + "T12:00:00").toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric", year: "numeric" });
const clock = (t: string) => new Date(t).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" });

function Day({ date }: { date: string }) {
  const { data } = useApi<JournalDay>(`journal/${date}`);
  if (!data) return <div className="px-5 py-6 text-[14px] text-ink-400">Loading…</div>;
  const last = data.captures[data.captures.length - 1];
  return (
    <div className="border-t border-ink-700 bg-ink-950/40 px-5 py-4">
      <p className="mb-3 flex flex-wrap items-center gap-2 text-[13px] text-ink-400">
        <Lock className="h-3.5 w-3.5" />Predictions recorded {clock(data.issued_at)}, before the 9:30 AM ET open, and locked since.
        {data.revised.length > 0 && <span>Replaced an earlier pre-open scan from {clock(data.revised[data.revised.length - 1])}.</span>}
        {data.status === "open" && last && <span>Latest live check: {clock(last.time)}.</span>}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-[13.5px]">
          <thead><tr className="text-left text-ink-500">
            <th className="py-2 pr-3 font-medium">Call</th><th className="px-2 font-medium">Stock</th>
            <th className="px-2 text-right font-medium" title="the session's opening price">Open</th>
            <th className="px-2 text-right font-medium">{data.status === "settled" ? "Close" : "Latest"}</th>
            <th className="px-2 text-right font-medium" title="open to close">Stock moved</th>
            <th className="px-2 text-right font-medium" title="the stock's move minus the average tradable stock's move">Against the market</th>
            <th className="px-2 text-right font-medium">Call was</th>
            <th className="px-2 text-right font-medium" title="on the position size shown on the day, after trading costs">Result</th>
            <th className="pl-2 text-right font-medium" title="only where live option quotes were captured near the open and near the close">Option</th>
          </tr></thead>
          <tbody>{data.ideas.map((i) => {
            const o = i.outcome, sgn = i.action === "BUY" ? 1 : -1;
            // the most recent live check that has a price for this stock (a throttled check can miss some)
            const lv = [...data.captures].reverse().find((c) => c.quotes[i.ticker]?.price != null)?.quotes[i.ticker];
            return (
              <tr key={i.ticker} className="border-t border-ink-800/70">
                <td className="py-2 pr-3"><Pill color={i.action === "BUY" ? "up" : "down"}>{i.action === "BUY" ? "Buy" : "Short"}</Pill></td>
                <td className="px-2"><Link to={`/t/${i.ticker}`} className="font-semibold hover:text-accent">{i.ticker}</Link>
                  <span className="ml-2 inline-block max-w-[180px] truncate align-bottom text-[12.5px] text-ink-500">{i.name}</span></td>
                <td className="num px-2 text-right">{o ? usd(o.open, 2) : "–"}</td>
                <td className="num px-2 text-right">{o ? usd(o.close, 2) : lv?.price != null ? usd(lv.price, 2) : "–"}</td>
                <td className={clsx("num px-2 text-right", tone(o ? o.stock_move : lv?.chg_open))}>{pct(o ? o.stock_move : lv?.chg_open, 2)}</td>
                <td className={clsx("num px-2 text-right", o ? tone(o.rel_move) : "text-ink-500")}>{o ? pct(o.rel_move, 2) : "–"}</td>
                <td className="px-2 text-right">{o ? (o.correct
                  ? <span className="inline-flex items-center gap-1 text-up"><Check className="h-3.5 w-3.5" />right</span>
                  : <span className="inline-flex items-center gap-1 text-down"><X className="h-3.5 w-3.5" />wrong</span>) : <span className="text-ink-500">pending</span>}</td>
                <td className={clsx("num px-2 text-right", o ? tone(o.pnl) : "text-ink-500")}>{o ? `${o.pnl >= 0 ? "+" : "−"}${usd(Math.abs(o.pnl), 2)}` : "–"}
                  {o && <span className="ml-1 text-[12px] text-ink-500">({pct(sgn * o.stock_move, 1)})</span>}</td>
                <td className={clsx("num pl-2 text-right", i.option_outcome ? tone(i.option_outcome.ret) : "text-ink-500")}>{i.option_outcome ? pct(i.option_outcome.ret, 0) : "–"}</td>
              </tr>);
          })}</tbody>
        </table>
      </div>
    </div>
  );
}

export default function Record() {
  const { data, error } = useApi<JournalIndex>("journal");
  const [open, setOpen] = useState<string | null>(null);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const t = data.tally;
  const first = open ?? data.days[0]?.date ?? null;
  return (
    <div>
      <PageHeader title="Track record"
        sub="Every morning's predictions, recorded before the market opens and locked, then marked against what happened by the close. This is the live record, separate from the backtest." />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Days settled" value={t.days} sub={t.since ? `record started ${longDay(t.since)}` : "no days yet"} />
        <Stat label="Calls right" value={t.ideas ? `${t.right} of ${t.ideas}` : "–"} sub={t.hit_rate == null ? "a coin flip would be half" : `${pct(t.hit_rate, 0, false)}; a coin flip would be 50%`}
          valueClass={t.hit_rate == null ? "" : t.hit_rate > 0.5 ? "text-up" : "text-down"} />
        <Stat label="Result so far" value={t.days ? `${t.pnl >= 0 ? "+" : "−"}${usd(Math.abs(t.pnl), 0)}` : "–"} sub={t.days ? `${pct(t.total_return, 2)} on the account, after costs` : "fills in after the first close"}
          valueClass={t.days ? tone(t.pnl) : ""} />
        <Stat label="What this proves" value={t.days < 60 ? "Too early" : "Building"} sub={`${t.days} day${t.days === 1 ? "" : "s"} is ${t.days < 60 ? "far too few to judge" : "a start"}. About 250 are needed to tell skill from luck.`} />
      </div>

      <Card className="mt-4" title="Day by day" pad={false}>
        {data.days.length === 0 && <p className="px-5 py-8 text-center text-[14px] text-ink-400">No predictions recorded yet. The first entry appears after the next morning scan.</p>}
        {data.days.map((d) => {
          const r = d.result, isOpen = first === d.date;
          return (
            <div key={d.date} className="border-t border-ink-700 first:border-0">
              <button onClick={() => setOpen(isOpen ? "" : d.date)} className="row-btn grid w-full grid-cols-[20px_1fr_auto] items-center gap-3 px-5 py-3 text-left">
                {isOpen ? <ChevronDown className="h-4 w-4 text-ink-400" /> : <ChevronRight className="h-4 w-4 text-ink-400" />}
                <span><span className="text-[15px] font-semibold text-ink-100">{longDay(d.date)}</span>
                  <span className="ml-3 text-[13px] text-ink-400">{d.ideas} predictions</span></span>
                {r ? (
                  <span className="flex flex-wrap items-center justify-end gap-x-5 gap-y-1 text-[13.5px]">
                    <span><b className={clsx("num", (r.hit_rate ?? 0) > 0.5 ? "text-up" : "text-down")}>{r.right} of {r.ideas}</b> <span className="text-ink-400">right</span></span>
                    <span className="text-ink-400">buys <b className={clsx("num", tone(r.long_move))}>{pct(r.long_move, 2)}</b> · shorts <b className={clsx("num", tone(-(r.short_move ?? 0)))}>{pct(r.short_move, 2)}</b></span>
                    <span className="text-ink-400">market <b className="num text-ink-200">{pct(r.market_move, 2)}</b></span>
                    <span><b className={clsx("num", tone(r.pnl))}>{r.pnl >= 0 ? "+" : "−"}{usd(Math.abs(r.pnl), 0)}</b></span>
                  </span>) : <Pill color="amber">Awaiting the close</Pill>}
              </button>
              {isOpen && <Day date={d.date} />}
            </div>);
        })}
      </Card>
      <p className="mt-4 max-w-[90ch] text-[12.5px] leading-relaxed text-ink-500">
        How a day is marked: each stock's move from that day's opening price to its closing price; "against the market" subtracts the average move of the tradable stocks the model ranked that morning.
        A buy is right when the stock beat that average, a short when it lagged it. Results use the position size shown on the day and include trading costs of 3 basis points each way.
        Each day's file is also committed to the repository, so the time of the commit is an independent record of when the predictions were made.
      </p>
    </div>
  );
}
