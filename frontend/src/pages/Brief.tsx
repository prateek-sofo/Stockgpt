import clsx from "clsx";
import { Flame, Newspaper, RefreshCw, Sparkles, Zap } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import TradeCard from "../components/TradeCard";
import { Card, ErrorBox, Loading, PageHeader, Pill, SentDot, Stat, Tabs } from "../components/ui";
import { clearCache, useApi } from "../lib/api";
import { pct, tone } from "../lib/format";
import type { Brief as B } from "../lib/types";

export default function Brief() {
  const { data, error } = useApi<B>("brief");
  const [tab, setTab] = useState<"stocks" | "options">("stocks");
  const [running, setRunning] = useState(false);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const list = tab === "options" ? data.options : data.stocks;
  const day = new Date(data.scan_date + "T12:00:00");
  const rescan = async () => {
    setRunning(true);
    await fetch("/api/run?mode=scan", { method: "POST" });
    const poll = setInterval(async () => {
      const s = await (await fetch("/api/run")).json();
      if (!s.running) { clearInterval(poll); clearCache(); setRunning(false); location.reload(); }
    }, 2500);
  };
  return (
    <div>
      <PageHeader
        title={<span className="flex items-center gap-3">Morning Brief <Pill color="accent"><Sparkles className="h-3 w-3" />{data.generated_for}</Pill></span>}
        sub={`${day.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" })} · ${data.universe_size} liquid names scanned of ${data.scanned}`}
        right={<button onClick={rescan} disabled={running}
          className="flex items-center gap-2 rounded-xl border border-ink-700 bg-ink-900 px-3.5 py-2 text-[12.5px] font-medium text-ink-200 transition hover:border-accent/50 hover:text-ink-100 disabled:opacity-50">
          <RefreshCw className={clsx("h-4 w-4", running && "animate-spin")} />{running ? "Scanning…" : "Re-run scan"}
        </button>}
      />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <Stat label="Market premarket" value={<span className={tone(data.market.premarket_gap)}>{pct(data.market.premarket_gap, 2)}</span>} sub="avg gap, liquid names" />
        <Stat label="Overnight news" value={data.market.news_items} sub="articles since prior close" />
        <Stat label="Median vol" value={pct(data.market.regime_vol, 0, false)} sub="20d realised, annualised" />
        <Stat label="Qualified trades" value={<span className={data.qualified.stocks + data.qualified.options ? "text-accent" : "text-ink-300"}>{data.qualified.stocks + data.qualified.options}</span>}
          sub={`${data.qualified.stocks} shares · ${data.qualified.options} options`} />
        <Stat label="Calibration" value={data.market.cal_slope.toFixed(2)} sub="raw edge × slope = honest edge" hint="Model confidence is shrunk by this factor, fitted on its own out-of-sample history" />
      </div>

      {data.qualified.stocks + data.qualified.options === 0 && (
        <div className="card mt-4 flex items-center gap-3 border-amber/30 px-5 py-3 text-[13px] text-ink-300">
          <Zap className="h-4 w-4 text-amber" />
          No setup clears the bar today (P ≥ {pct(data.thresholds.min_prob, 0, false)}, option EV ≥ {pct(data.thresholds.min_ev, 0, false)} after costs).
          The board below is the ranked watchlist. Sitting out is a position.
        </div>
      )}

      <div className="mt-7 flex items-center justify-between">
        <h2 className="text-[16px] font-semibold">Highest-opportunity setups</h2>
        <Tabs value={tab} onChange={setTab} items={[
          { v: "stocks", label: `Day trades · shares (${data.stocks.length})` },
          { v: "options", label: `Options (${data.options.length})` },
        ]} />
      </div>
      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2 2xl:grid-cols-3">
        {list.map((t, i) => <TradeCard key={t.ticker + t.kind} t={t} rank={i + 1} />)}
      </div>

      <div className="mt-8 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title={<span className="flex items-center gap-2"><Flame className="h-4 w-4 text-amber" />Earnings tonight / before tomorrow's open</span>}
          right={<span className="text-[11.5px] text-ink-500">implied move vs this ticker's own history</span>}>
          {data.straddles.length === 0 ? <div className="py-6 text-center text-[13px] text-ink-500">No liquid reports in the next session.</div> : (
            <table className="w-full text-[12.5px]">
              <thead><tr className="text-left text-ink-500">
                <th className="py-2 font-medium">Ticker</th><th className="font-medium">Implied</th><th className="font-medium">Hist. avg</th>
                <th className="font-medium">Edge</th><th className="font-medium">Signal</th></tr></thead>
              <tbody>{data.straddles.map((s) => (
                <tr key={s.ticker} className="border-t border-ink-800">
                  <td className="py-2.5"><Link to={`/t/${s.ticker}`} className="font-semibold hover:text-accent">{s.ticker}</Link>
                    <div className="text-[11px] text-ink-500">{s.name}</div></td>
                  <td className="num">±{pct(s.implied_move, 1, false)}</td>
                  <td className="num">{s.hist_move ? `±${pct(s.hist_move, 1, false)}` : "–"}</td>
                  <td className="num">{s.edge_ratio ? `${s.edge_ratio.toFixed(2)}×` : "–"}</td>
                  <td><Pill color={s.signal.startsWith("BUY") ? "up" : s.signal.startsWith("SELL") ? "down" : "ink"}>{s.signal}</Pill></td>
                </tr>))}</tbody>
            </table>
          )}
        </Card>
        <Card title={<span className="flex items-center gap-2"><Newspaper className="h-4 w-4 text-sky" />Premarket movers</span>}>
          <div className="space-y-1">
            {data.movers.map((m) => (
              <Link key={m.ticker} to={`/t/${m.ticker}`} className="flex items-center gap-3 rounded-lg px-2 py-1.5 text-[12.5px] hover:bg-ink-800">
                <SentDot s={m.sentiment} />
                <span className="w-14 font-semibold">{m.ticker}</span>
                <span className={clsx("num w-16", tone(m.gap))}>{pct(m.gap, 2)}</span>
                <span className="num text-ink-500">{m.news} news</span>
                <span className={clsx("num ml-auto", m.prob_up >= 0.5 ? "text-up" : "text-down")}>{pct(m.prob_up, 1, false)} ↑</span>
              </Link>
            ))}
          </div>
        </Card>
      </div>

      <Card className="mt-4" title="Sector tilt" right={<span className="text-[11.5px] text-ink-500">mean directional conviction · overnight news count</span>}>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-6">
          {[...data.sectors].sort((a, b) => b.score - a.score).map((s) => {
            const a = Math.min(1, Math.abs(s.score) / 0.01);
            return (
              <div key={s.sector} className="rounded-xl border border-ink-800 p-3"
                style={{ background: s.score >= 0 ? `rgba(46,230,166,${0.04 + a * 0.16})` : `rgba(255,92,122,${0.04 + a * 0.16})` }}>
                <div className="truncate text-[12px] font-medium text-ink-200">{s.sector}</div>
                <div className="mt-1 flex items-baseline justify-between">
                  <span className={clsx("num text-[13px]", tone(s.score))}>{(s.score * 1e4).toFixed(0)}</span>
                  <span className="num text-[10.5px] text-ink-500">{s.news} news</span>
                </div>
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}
