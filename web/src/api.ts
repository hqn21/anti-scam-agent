import type { AnalysisRow, Curated, JobStatus, Stats } from "./types";

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}

export const api = {
  analyze: (url: string, source = "web") =>
    fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, source }),
    }).then(j<{ id: string; status: string }>),
  status: (id: string) => fetch(`/api/analyze/${id}`).then(j<JobStatus>),
  list: (limit = 50, offset = 0) =>
    fetch(`/api/analyses?limit=${limit}&offset=${offset}`).then(j<AnalysisRow[]>),
  detail: (id: string) => fetch(`/api/analyses/${id}`).then(j<Curated>),
  stats: () => fetch("/api/stats").then(j<Stats>),
};
