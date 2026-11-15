const ok = (v: number | null | undefined): v is number => v != null && isFinite(v);

export const pct = (v: number | null | undefined, d = 1, sign = true) =>
  !ok(v) ? "–" : `${sign && v > 0 ? "+" : ""}${(v * 100).toFixed(d)}%`;

export const num = (v: number | null | undefined, d = 2) =>
  !ok(v) ? "–" : v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

export const usd = (v: number | null | undefined, d = 0) =>
  !ok(v) ? "–" : `$${v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;

export const compact = (v: number | null | undefined, prefix = "") => {
  if (!ok(v)) return "–";
  const a = Math.abs(v);
  const s =
    a >= 1e12 ? `${(v / 1e12).toFixed(1)}T` : a >= 1e9 ? `${(v / 1e9).toFixed(1)}B` : a >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : a >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : v.toFixed(0);
  return prefix + s;
};

export const bps = (v: number | null | undefined) => (!ok(v) ? "–" : `${v > 0 ? "+" : ""}${v.toFixed(1)} bps`);

export const tone = (v: number | null | undefined) =>
  !ok(v) ? "text-ink-300" : v > 0 ? "text-up" : v < 0 ? "text-down" : "text-ink-300";

export const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export const TRAIT_LABEL: Record<string, { name: string; pos: string; neg: string }> = {
  news_on_wsent: { name: "News drift", pos: "keeps moving with news after the open", neg: "fades news after the open" },
  gap_pre_z: { name: "Gap behaviour", pos: "extends its opening gap", neg: "fades its opening gap" },
  ret_1d_z: { name: "Day-to-day", pos: "continues yesterday's move", neg: "reverses yesterday's move" },
  eps_surprise: { name: "Earnings drift", pos: "drifts with the EPS surprise", neg: "reverses the EPS surprise" },
};
