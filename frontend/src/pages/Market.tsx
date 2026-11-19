import clsx from "clsx";
import { ExternalLink, ShieldAlert, TrendingUp } from "lucide-react";
import { Link } from "react-router-dom";
import { Card, ErrorBox, Loading, PageHeader, Pill, ToneDot, TonePill } from "../components/ui";
import { useApi } from "../lib/api";
import { pct, tone, usd } from "../lib/format";
import type { MacroPoint, Market as MarketData, Mover } from "../lib/types";

const day = (s: string) => new Date(s.length > 10 ? s : s + "T12:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });

function Points({ items, empty }: { items: MacroPoint[]; empty: string }) {
  if (!items.length) return <p className="py-3 text-[14px] text-ink-400">{empty}</p>;
  return (
    <ul className="divide-y divide-ink-800">
      {items.map((p, i) => (
        <li key={i} className="py-3">
          <div className="text-[14.5px] leading-snug text-ink-100">{p.text}</div>
          <div className="num mt-1 text-[12.5px] text-ink-400">{p.value} · as of {day(p.as_of)}</div>
        </li>))}
    </ul>
  );
}

function Movers({ title, rows }: { title: string; rows: Mover[] }) {
  return (
    <Card title={title} pad={false}>
      <ul className="divide-y divide-ink-800 px-5 pb-3">
        {rows.map((m) => (
          <li key={m.ticker} className="flex items-baseline gap-3 py-2.5">
            <Link to={`/t/${m.ticker}`} className="w-14 shrink-0 font-semibold hover:text-accent">{m.ticker}</Link>
            <span className={clsx("num w-16 shrink-0 text-right", tone(m.chg))}>{pct(m.chg, 1)}</span>
            <span className="min-w-0 flex-1 truncate text-[13px] text-ink-400" title={m.line}>{m.line}</span>
            <span className="num shrink-0 text-[13px] text-ink-400">{usd(m.price, 2)}</span>
          </li>))}
      </ul>
    </Card>
  );
}

export default function Market() {
  const { data, error } = useApi<MarketData>("market");
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const m = data.macro, f = data.filings;
  const topics = [...new Set(m.headlines.map((h) => h.topic))];
  const color = m.verdict.startsWith("More supports") ? "up" : m.verdict.startsWith("More risks") ? "down" : "amber";
  return (
    <div>
      <PageHeader title="Market" sub={`What is helping stocks and what could hurt them. Prices through ${day(data.as_of)}; every line shows the number behind it and its date.`} />

      <div className="card flex flex-wrap items-center gap-x-5 gap-y-2 px-5 py-4">
        <Pill color={color} className="!text-[14px]">{m.verdict}</Pill>
        <span className="text-[15px] text-ink-100">{m.line}</span>
        <span className="basis-full text-[13px] leading-relaxed text-ink-400">{m.note}</span>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title={<span className="flex items-center gap-2 text-up"><TrendingUp className="h-4 w-4" />What helps the bull case ({m.bull.length})</span>}>
          <Points items={m.bull} empty="Nothing in the data is clearly supportive right now." />
        </Card>
        <Card title={<span className="flex items-center gap-2 text-down"><ShieldAlert className="h-4 w-4" />Risks ({m.risks.length})</span>}>
          <Points items={m.risks} empty="Nothing in the data is flashing a warning right now." />
        </Card>
      </div>
      {m.neutral.length > 0 && (
        <Card className="mt-4" title={`Neither helping nor hurting (${m.neutral.length})`}>
          <ul className="grid gap-x-8 gap-y-2 md:grid-cols-2">
            {m.neutral.map((p, i) => <li key={i} className="text-[13.5px] text-ink-300">{p.text} <span className="num text-ink-500">{p.value}</span></li>)}
          </ul>
        </Card>)}

      {topics.length > 0 && (
        <Card className="mt-4" title="In the news: economy, policy and politics"
          right={data.news_through && <span className="num text-[12.5px] text-ink-400">latest {new Date(data.news_through).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</span>}>
          <div className="grid gap-x-8 gap-y-5 md:grid-cols-2">
            {topics.map((t) => (
              <div key={t}>
                <div className="label mb-1.5">{t}</div>
                <ul className="space-y-2">
                  {m.headlines.filter((h) => h.topic === t).map((h, i) => (
                    <li key={i} className="flex gap-2.5">
                      <ToneDot t={h.tone} />
                      <a href={h.url} target="_blank" rel="noreferrer" className="text-[14px] leading-snug text-ink-100 hover:text-accent">
                        {h.headline} <span className="whitespace-nowrap text-[12.5px] text-ink-500">{h.source} · {day(h.time)}</span>
                      </a>
                    </li>))}
                </ul>
              </div>))}
          </div>
          <p className="mt-4 text-[12.5px] text-ink-500">Dot colour is the tone of the headline's wording (green positive, red negative), scored by a word list. Headlines link to the publisher.</p>
        </Card>)}

      {f && (
        <Card className="mt-4" title={`Important company filings with the SEC on ${day(f.date)}`}
          right={<span className="num text-[12.5px] text-ink-400">{f.total.toLocaleString()} filings by {f.companies.toLocaleString()} listed companies that day</span>} pad={false}>
          <div className="max-h-[520px] overflow-auto">
            <table className="w-full text-[13.5px]">
              <thead className="sticky top-0 bg-ink-900"><tr className="text-left text-ink-500">
                <th className="px-5 py-2.5 font-medium">Company</th><th className="px-2 font-medium">What happened</th><th className="px-2 font-medium">Why it matters</th><th className="px-5 font-medium"></th>
              </tr></thead>
              <tbody>{f.important.map((x, i) => (
                <tr key={i} className="border-t border-ink-800/70 align-top">
                  <td className="whitespace-nowrap px-5 py-2.5"><Link to={`/t/${x.ticker}`} className="font-semibold hover:text-accent">{x.ticker}</Link>
                    <div className="max-w-[180px] truncate text-[12.5px] text-ink-500">{x.name}</div></td>
                  <td className="px-2 py-2.5"><div className="text-ink-100">{x.title}</div><div className="mt-1"><TonePill t={x.tone} /></div></td>
                  <td className="px-2 py-2.5 text-ink-400">{x.why}</td>
                  <td className="px-5 py-2.5 text-right"><a href={x.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 whitespace-nowrap text-ink-400 hover:text-accent">Form {x.form}<ExternalLink className="h-3 w-3" /></a></td>
                </tr>))}</tbody>
            </table>
          </div>
        </Card>)}

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Movers title={`Biggest gains on ${day(data.as_of)}`} rows={data.movers.up} />
        <Movers title={`Biggest drops on ${day(data.as_of)}`} rows={data.movers.down} />
      </div>
      <p className="mt-4 text-[12.5px] text-ink-500">Tracking {data.counts.listed.toLocaleString()} listed tickers ({Object.entries(data.counts.by_exchange).map(([k, v]) => `${k} ${v.toLocaleString()}`).join(", ")}); {data.counts.priced.toLocaleString()} have a current price. Movers are limited to stocks trading at least $5M a day.</p>
    </div>
  );
}
