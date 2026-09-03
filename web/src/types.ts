// Shapes mirror the FastAPI responses in paper_manager/server.py.

export interface PaperNode {
  id: number;
  title: string;
  authors: string | null;
  year: number | null;
  venue: string | null;
  summary: string;
  cited_by_count: number;
  n_chunks: number;
  has_citations: boolean;
  cluster: number;
}

export interface GraphEdge {
  src: number;
  dst: number;
  kind: "citation" | "similar";
  weight?: number;
}

export interface GraphData {
  nodes: PaperNode[];
  edges: GraphEdge[];
}

export interface RelatedItem {
  paper_id: number;
  title: string;
  year: number | null;
  similarity?: number;
}

export interface PaperDetail {
  id: number;
  title: string;
  authors: string | null;
  year: number | null;
  venue: string | null;
  doi: string | null;
  abstract: string | null;
  summary: string | null;
  engine: string;
  added_at: string;
  citations_fetched_at: string | null;
  cited_by_count: number | null;
  kg_built_at: string | null;
  cites_in_lib: number;
  cited_by_in_lib: number;
  related: {
    cites: RelatedItem[];
    cited_by: RelatedItem[];
    semantic: RelatedItem[];
  };
  ext_counts: Record<string, number>;
}

export interface SearchHit {
  paper_id: number;
  title: string;
  year: number | null;
  summary: string;
  chunk_id: number;
  section: string;
  pages: string | null;
  score: number;
  matched_chunks: number;
  snippet: string;
}

export interface KgNode {
  id: number;
  name: string;
  type: "method" | "dataset" | "task" | "concept" | string;
  desc: string | null;
  n_chunks: number;
  paper_ids: number[];
}

export interface KgEdge {
  src: number;
  dst: number;
  relation: string;
  paper_id: number | null;
}

export interface KgData {
  nodes: KgNode[];
  edges: KgEdge[];
}

export interface EntityDetail {
  id: number;
  name: string;
  type: string;
  desc: string | null;
  neighbors: {
    id: number;
    name: string;
    type: string;
    relation: string;
    direction: string;
  }[];
  papers: RelatedItem[];
}

export interface Status {
  papers: number;
  chunks: number;
  vectors: number;
  db_path: string;
}

export interface IngestReport {
  status: string;
  paper_id?: number;
  title?: string;
  chunks?: number;
  cost_usd?: number | null;
}

export interface FetchCitationsReport {
  status: string;
  refs?: number;
  cited?: number;
  cited_by_count?: number | null;
}

export type ViewMode = "timeline" | "force" | "kg";
export type SizeMode = "cited" | "chunks" | "uniform";

export interface Filters {
  yearMin?: number;
  yearMax?: number;
  author?: string;
  venue?: string;
}
