import type {
  EntityDetail,
  FetchCitationsReport,
  GraphData,
  IngestReport,
  KgData,
  PaperDetail,
  SearchHit,
  Status,
} from "./types";

async function json<T>(path: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      msg = (await r.json()).detail || msg;
    } catch {
      /* keep status text */
    }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

export const api = {
  status: () => json<Status>("/api/status"),
  graph: (sim: number) => json<GraphData>(`/api/graph?sim=${sim.toFixed(2)}`),
  paper: (id: number) => json<PaperDetail>(`/api/paper/${id}`),
  search: (q: string, topK = 8) =>
    json<{ hits: SearchHit[] }>(`/api/search?q=${encodeURIComponent(q)}&top_k=${topK}`),
  kgGraph: () => json<KgData>("/api/kg/graph"),
  entity: (id: number) => json<EntityDetail>(`/api/kg/entity/${id}`),
  fetchCitations: (id: number) =>
    json<FetchCitationsReport>(`/api/fetch-citations/${id}`, { method: "POST" }),
  ingest: (file: File, engine: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("engine", engine);
    return json<IngestReport>("/api/ingest", { method: "POST", body: fd });
  },
  markdown: async (id: number): Promise<string> => {
    const r = await fetch(`/api/paper/${id}/markdown`);
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.text();
  },
  pdfUrl: (id: number) => `/api/paper/${id}/pdf`,
};
