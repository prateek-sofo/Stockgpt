import { Cpu, Database, FileJson, GitBranch, Play } from "lucide-react";
import { useState } from "react";
import { Card, ErrorBox, Loading, PageHeader, Pill } from "../components/ui";
import { clearCache, STATIC, useApi } from "../lib/api";
import type { Meta } from "../lib/types";

const TABLES = [
  { n: "news", cols: "published_at (UTC), ticker, headline, [body, source, url]", note: "One row per article × ticker. published_at must be the real publication time, not scrape time." },
  { n: "prices", cols: "date, ticker, open, high, low, close, volume, [premarket]", note: "Daily, adjusted. premarket = ~09:00 ET print; lets the scan see the gap." },
  { n: "earnings", cols: "ticker, report_date, timing (BMO|AMC), [eps_actual, eps_estimate]", note: "Include future scheduled dates with blank actuals." },
  { n: "fundamentals", cols: "ticker, report_date, revenue_growth_yoy, gross_margin, operating_margin, fcf_margin, …", note: "Keyed by the date the numbers became public." },
  { n: "options", cols: "date, ticker, iv30, option_volume, [iv_weekly, atm_spread_pct]", note: "Daily summary. Sources: ORATS, ThetaData, Polygon, CBOE." },
  { n: "tickers", cols: "ticker, name, sector, [market_cap]", note: "One row per symbol." },
];

const STEPS = [
  { t: "Score news", d: "Lexicon scorer by default. Set STOCKGPT_LLM_SCORING=1 to score headlines with Claude (structured JSON: sentiment, materiality, event type)." },
  { t: "Build features", d: "Point-in-time panel. News is bucketed by the first 09:00 ET scan that could see it; prices through the prior close plus premarket." },
  { t: "Learn each ticker", d: "Per-ticker behaviour (news drift, gap habit, reversal, earnings drift) as shrunk point-in-time slopes. New names default to the market." },
  { t: "Predict", d: "Ridge core with ticker interactions + LightGBM, in vol units. Separate model for move size. Probability calibrated on its own out-of-sample record." },
  { t: "Price the trade", d: "Integrates option P&L over the predicted move distribution, per strike. Option EV is then corrected by its own track record." },
  { t: "Size + filter", d: "Liquidity gate, ¼ Kelly, per-trade and per-day caps, drawdown brake." },
];

export default function Data() {
  const { data, error } = useApi<Meta>("meta");
  const [msg, setMsg] = useState<string | null>(null);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const run = async (mode: "scan" | "full") => {
    setMsg(`Running ${mode}…`);
    await fetch(`/api/run?mode=${mode}`, { method: "POST" });
    const poll = setInterval(async () => {
      const s = await (await fetch("/api/run")).json();
      if (!s.running) { clearInterval(poll); clearCache(); setMsg(`Done. ${s.log.split("\n").filter(Boolean).slice(-1)[0] ?? ""}`); }
    }, 3000);
  };
  return (
    <div>
      <PageHeader title="Data & Models" sub="Plug in your scraped data; everything downstream rebuilds from it." />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card title={<span className="flex items-center gap-2"><Database className="h-4 w-4 text-sky" />Current dataset</span>}>
          <dl className="grid grid-cols-2 gap-y-2 text-[13px]">
            <dt className="text-ink-400">Source</dt><dd>{data.source === "synthetic" ? <Pill color="amber">synthetic demo</Pill> : <Pill color="up">your data</Pill>}</dd>
            <dt className="text-ink-400">Range</dt><dd className="num">{data.data_start} → {data.data_end}</dd>
            <dt className="text-ink-400">Tickers</dt><dd className="num">{data.tickers.toLocaleString()}</dd>
            <dt className="text-ink-400">Liquid universe</dt><dd className="num">{data.universe}</dd>
            <dt className="text-ink-400">News rows</dt><dd className="num">{data.news.toLocaleString()}</dd>
            <dt className="text-ink-400">Features</dt><dd className="num">{data.features}</dd>
            <dt className="text-ink-400">Last run</dt><dd className="num">{new Date(data.generated_at).toLocaleString()}</dd>
            <dt className="text-ink-400">Runtime</dt><dd className="num">{data.runtime_sec}s</dd>
          </dl>
          <div className={STATIC ? "hidden" : "mt-5 flex gap-2"}>
            <button onClick={() => run("scan")} className="flex items-center gap-2 rounded-xl bg-accent px-3.5 py-2 text-[13.5px] font-semibold text-white hover:brightness-110"><Play className="h-3.5 w-3.5" />Morning scan</button>
            <button onClick={() => run("full")} className="flex items-center gap-2 rounded-xl border border-ink-700 px-3.5 py-2 text-[13.5px] font-medium text-ink-200 hover:border-ink-500"><Cpu className="h-3.5 w-3.5" />Retrain + backtest</button>
          </div>
          {msg && <div className="num mt-3 text-[12.5px] text-ink-400">{msg}</div>}
        </Card>
        <Card className="xl:col-span-2" title={<span className="flex items-center gap-2"><FileJson className="h-4 w-4 text-accent" />Data contract: drop parquet or CSV into data/raw/</span>}>
          <div className="divide-y divide-ink-800">
            {TABLES.map((t) => (
              <div key={t.n} className="grid grid-cols-[110px_1fr] gap-3 py-2.5 text-[13.5px]">
                <code className="num font-semibold text-accent">{t.n}</code>
                <div><div className="num text-ink-200">{t.cols}</div><div className="mt-0.5 text-[12.5px] text-ink-500">{t.note}</div></div>
              </div>))}
          </div>
        </Card>
      </div>
      <Card className="mt-4" title={<span className="flex items-center gap-2"><GitBranch className="h-4 w-4 text-up" />Pipeline</span>}>
        <ol className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
          {STEPS.map((s, i) => (
            <li key={s.t} className="rounded-xl border border-ink-800 bg-ink-950/40 p-4">
              <div className="num text-[12.5px] text-accent">0{i + 1}</div>
              <div className="mt-1 text-[13px] font-semibold">{s.t}</div>
              <div className="mt-1.5 text-[13px] leading-relaxed text-ink-400">{s.d}</div>
            </li>))}
        </ol>
      </Card>
    </div>
  );
}
