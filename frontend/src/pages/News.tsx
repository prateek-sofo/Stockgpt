import clsx from "clsx";
import { ExternalLink, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";
import { Card, ErrorBox, Loading, PageHeader, Pill, Stat, ToneDot } from "../components/ui";
import { STATIC, useLive } from "../lib/api";
import { pct, tone, usd } from "../lib/format";
import type { Intraday, StudyTest } from "../lib/types";

const RULE: Record<string, string> = { first_move: "Follow the stock's move so far", tone: "Follow the news (good up, bad down)", model: "Take the morning scan's side" };
const HOR: Record<string, string> = { m30: "30 minutes later", m60: "60 minutes later", close: "at the close" };
const clock = (t: string) => new Date(t).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const lag = (s: number) => (s < 90 ? `${s} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`);

function Study({ title, tests, pick }: { title: string; tests: StudyTest[]; pick: (t: StudyTest) => boolean }) {
  const rows = tests.filter(pick);
  if (!rows.length) return null;
  return (
    <div>
      <div className="t-head mb-1">{title}</div>
      <table className="w-full text-[13px]">
        <thead><tr className="text-left"><th className="py-1.5 pr-3">Rule</th><th className="px-2 text-right">Cases</th><th className="px-2 text-right" title="average result per trade before costs, in hundredths of a percent">Before costs</th>
          <th className="px-2 text-right" title="after 10 basis points for getting in and out">After costs</th><th className="pl-2 text-right">Right</th></tr></thead>
        <tbody>{rows.map((t) => (
          <tr key={t.name} className="border-t border-ink-700">
            <td className="py-1.5 pr-3 text-ink-200">{t.name.split(" | ").filter((_, i) => i !== 1).join(", ")}</td>
            <td className="num px-2 text-right text-ink-400">{t.n.toLocaleString()}</td>
            <td className={clsx("num px-2 text-right", tone(t.mean_bp))}>{t.mean_bp >= 0 ? "+" : ""}{(t.mean_bp / 100).toFixed(2)}%</td>
            <td className={clsx("num px-2 text-right font-medium", tone(t.net_bp))}>{t.net_bp >= 0 ? "+" : ""}{(t.net_bp / 100).toFixed(2)}%</td>
            <td className="num pl-2 text-right text-ink-400">{pct(t.win, 0, false)}</td>
          </tr>))}</tbody>
      </table>
    </div>
  );
}

export default function News() {
  const { data, error } = useLive<Intraday>("intraday", 30000);
  if (error) return <ErrorBox error={STATIC ? "The live news loop runs with the app, not in a saved copy." : `${error}. Start it with: python -m stockgpt.intraday loop`} />;
  if (!data) return <Loading />;
  const sc = data.scorecard;
  const day = data.study?.["C. overnight events, daily bars"], hour = data.study?.["B. hourly bars, 2 years"];
  return (
    <div>
      <PageHeader
        title={<>{sc.alerts} alerts since the loop started. <span className="text-ink-400">Checked every {Math.round(data.interval_s / 60)} minutes.</span></>}
        sub="Each new piece of company news is stamped with the live price when it is seen, then marked 30 and 60 minutes later and at the close. The rules below are being tested, not recommended." />

      <section className="read px-5 py-4">
        <div className="flex items-center gap-2"><Sparkles className="h-3.5 w-3.5 text-accent" /><span className="t-label !text-accent">What history says about trading on news</span></div>
        <p className="mt-2 max-w-[88ch] text-[14.5px] font-medium leading-snug text-ink-100">
          Reacting to the fact of a company event, in the direction the stock first moves, has not paid after costs in liquid stocks.
        </p>
        <ul className="mt-2.5 space-y-1.5 text-[13.5px] text-ink-200">
          {hour && <li className="flex gap-2.5"><span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-ink-400" /><span>During market hours ({hour.events.toLocaleString()} filings over two years): after the first move there is no further drift an hour later or by the close.</span></li>}
          {day && <li className="flex gap-2.5"><span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-ink-400" /><span>Overnight ({day.events.toLocaleString()} filings since 2019): following the opening gap through the day earns a few hundredths of a percent before costs and loses after them.</span></li>}
          <li className="flex gap-2.5"><span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-amber" /><span>Not yet tested: what the news says. That needs Sofo World's article history, which is {data.connected.sofo ? "connected" : "not connected"}.</span></li>
        </ul>
      </section>

      <div className="mt-6 grid grid-cols-2 gap-x-8 gap-y-5 md:grid-cols-4">
        <Stat label="Sources on" value={data.connected.sofo ? "2" : "1"} sub={data.connected.sofo ? "Sofo World news, SEC filings" : "SEC filings in real time. Sofo World news not connected"} />
        <Stat label="Seen after publication" value={sc.median_lag_s == null ? "–" : lag(sc.median_lag_s)} sub="median delay from publication to alert" />
        <Stat label="Alerts marked" value={sc.rows.reduce((a, r) => Math.max(a, r.n), 0)} sub="with at least one later price" />
        <Stat label="Last cycle" value={new Date(data.generated_at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })} sub={`running since ${clock(data.started)}`} />
      </div>

      <Card className="mt-8" title="Live scorecard: each rule, marked against the S&P 500 over the same minutes">
        {sc.rows.length === 0 ? <p className="t-body py-3 text-ink-400">No alert has a later price yet. The first marks appear 30 minutes after the first alert during market hours.</p> : (
          <table className="w-full text-[13.5px]">
            <thead><tr className="text-left"><th className="py-2 pr-3">Rule</th><th className="px-2">Measured</th><th className="px-2 text-right">Alerts</th><th className="px-2 text-right">Average, before costs</th><th className="px-2 text-right">After costs</th><th className="pl-2 text-right">Right</th></tr></thead>
            <tbody>{sc.rows.map((r) => (
              <tr key={r.rule + r.horizon} className="border-t border-ink-700">
                <td className="py-2 pr-3 text-ink-100">{RULE[r.rule]}</td><td className="px-2 text-ink-400">{HOR[r.horizon]}</td>
                <td className="num px-2 text-right">{r.n}</td>
                <td className={clsx("num px-2 text-right", tone(r.mean_bp))}>{(r.mean_bp / 100).toFixed(2)}%</td>
                <td className={clsx("num px-2 text-right font-medium", tone(r.net_bp))}>{(r.net_bp / 100).toFixed(2)}%</td>
                <td className="num pl-2 text-right text-ink-400">{pct(r.win, 0, false)}</td>
              </tr>))}</tbody>
          </table>)}
      </Card>

      <Card className="mt-8" title="Alerts, newest first" pad={false}>
        {data.alerts.length === 0 && <p className="px-5 py-8 text-center text-[13.5px] text-ink-400">Nothing new since the loop started. Alerts appear here within two minutes of a filing or article.</p>}
        <ul className="divide-y divide-ink-700">
          {data.alerts.map((a) => (
            <li key={a.id} className="grid grid-cols-[1fr_auto] gap-x-6 gap-y-1 px-5 py-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                  <ToneDot t={a.tone} />
                  <Link to={`/t/${a.ticker}`} className="text-[14px] font-semibold text-ink-100 hover:text-accent">{a.ticker}</Link>
                  <span className="text-[13.5px] text-ink-100">{a.headline}</span>
                  {a.rules.model && <Pill color="accent">{a.rules.model > 0 ? "scan: buy" : "scan: short"}</Pill>}
                </div>
                <div className="t-small mt-0.5 pl-[18px]">{a.name} · {a.source} · published {clock(a.published_at)} · seen {lag(a.lag_s)} later
                  {a.url && <a href={a.url} target="_blank" rel="noreferrer" className="ml-2 inline-flex items-center gap-1 hover:text-accent">open<ExternalLink className="h-3 w-3" /></a>}</div>
              </div>
              <div className="text-right">
                <div className="num text-[13.5px] text-ink-100">{usd(a.price, 2)} <span className={clsx("ml-1", tone(a.chg_day))}>{pct(a.chg_day, 1)}</span></div>
                <div className="t-small num">
                  {(["m30", "m60", "close"] as const).map((k) => a.marks[k] && (
                    <span key={k} className="ml-3">{k === "m30" ? "30m" : k === "m60" ? "60m" : "close"} <b className={clsx("font-medium", tone(a.marks[k]!.rel))}>{pct(a.marks[k]!.rel, 1)}</b></span>))}
                  {!Object.keys(a.marks).length && "awaiting first mark"}
                </div>
              </div>
            </li>))}
        </ul>
      </Card>

      {data.study && (
        <Card className="mt-8" title="The history behind the read above">
          <div className="grid gap-x-10 gap-y-6 lg:grid-cols-2">
            {day && <Study title={`Overnight filings, ${day.period[0].slice(0, 4)} to ${day.period[1].slice(0, 4)}: follow the opening gap`} tests={day.tests} pick={(t) => /^(all overnight events|gap over 5%|results, gap up over 5%|not results, gap over 2%)/.test(t.name)} />}
            {hour && <Study title="Filings during market hours, two years: follow the first move" tests={hour.tests} pick={(t) => /^(all events|first move over 1%|results)/.test(t.name)} />}
          </div>
          <p className="t-small mt-3 max-w-[92ch]">Results are measured against the market over the same period. A filing's time is when the SEC accepted it; a company's press release often goes out minutes earlier, which is one reason the first move is already over.</p>
        </Card>)}
    </div>
  );
}
