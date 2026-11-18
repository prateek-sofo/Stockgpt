import { useEffect, useState } from "react";

export const STATIC = import.meta.env.VITE_STATIC === "1";
const cache = new Map<string, unknown>();

export async function getJSON<T>(path: string): Promise<T> {
  if (cache.has(path)) return cache.get(path) as T;
  // Static snapshot build (VITE_STATIC=1): read bundled result files instead of the live API
  const url = STATIC ? `data/${path.replace(/^ticker\//, "tickers/")}.json` : `/api/${path}`;
  const r = await fetch(url);
  if (!r.ok) throw new Error(STATIC ? "not included in this snapshot (run the app locally for every ticker)"
    : (await r.json().catch(() => ({}))).detail ?? r.statusText);
  const j = (await r.json()) as T;
  cache.set(path, j);
  return j;
}

export function clearCache() {
  cache.clear();
}

export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(path && cache.has(path) ? (cache.get(path) as T) : null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return;
    let live = true;
    setError(null);
    if (!cache.has(path)) setData(null);
    getJSON<T>(path)
      .then((d) => live && setData(d))
      .catch((e) => live && setError(String(e.message ?? e)));
    return () => {
      live = false;
    };
  }, [path]);
  return { data, error };
}
