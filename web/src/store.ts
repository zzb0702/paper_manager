import { create } from "zustand";
import { api } from "./api";
import type {
  Filters,
  GraphData,
  KgData,
  PaperNode,
  SearchHit,
  SizeMode,
  ViewMode,
} from "./types";

export interface Toast {
  id: number;
  msg: string;
  kind: "info" | "ok" | "err";
}

interface AppState {
  graph: GraphData;
  byId: Map<number, PaperNode>;
  kg: KgData | null;
  viewMode: ViewMode;
  filters: Filters | null;
  isolateCluster: number | null;
  selectedPaper: number | null;
  selectedEntity: number | null;
  sizeMode: SizeMode;
  showCitation: boolean;
  showSimilar: boolean;
  sim: number;
  hits: SearchHit[] | null;
  toasts: Toast[];
  loading: number;
  markdownFor: number | null;

  load: () => Promise<void>;
  loadKg: () => Promise<void>;
  setView: (v: ViewMode) => void;
  toggleCluster: (c: number) => void;
  selectPaper: (pid: number) => void;
  showEntity: (eid: number) => void;
  closePanel: () => void;
  setFilters: (f: Filters | null) => void;
  setSizeMode: (m: SizeMode) => void;
  setShowCitation: (on: boolean) => void;
  setShowSimilar: (on: boolean) => void;
  setSim: (v: number) => void;
  setHits: (h: SearchHit[] | null) => void;
  toast: (msg: string, kind?: Toast["kind"]) => void;
  openMarkdown: (pid: number) => void;
  closeMarkdown: () => void;
}

let toastSeq = 0;
let simTimer: number | undefined;

function syncUrl(s: AppState) {
  const p = new URLSearchParams();
  if (s.viewMode !== "timeline") p.set("view", s.viewMode);
  if (s.selectedPaper != null) p.set("paper", String(s.selectedPaper));
  if (s.selectedEntity != null) p.set("entity", String(s.selectedEntity));
  const qs = p.toString();
  history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
}

export const useApp = create<AppState>((set, get) => ({
  graph: { nodes: [], edges: [] },
  byId: new Map(),
  kg: null,
  viewMode: "timeline",
  filters: null,
  isolateCluster: null,
  selectedPaper: null,
  selectedEntity: null,
  sizeMode: "cited",
  showCitation: true,
  showSimilar: true,
  sim: 0.55,
  hits: null,
  toasts: [],
  loading: 0,
  markdownFor: null,

  load: async () => {
    set((s) => ({ loading: s.loading + 1 }));
    try {
      const g = await api.graph(get().sim);
      const byId = new Map(g.nodes.map((n) => [n.id, n]));
      set({ graph: g, byId });
      if (get().viewMode === "kg" && !get().kg) await get().loadKg();
    } catch (e) {
      get().toast(`加载失败: ${e instanceof Error ? e.message : e}`, "err");
    } finally {
      set((s) => ({ loading: s.loading - 1 }));
    }
  },

  loadKg: async () => {
    set((s) => ({ loading: s.loading + 1 }));
    try {
      set({ kg: await api.kgGraph() });
    } catch (e) {
      get().toast(`概念图加载失败: ${e instanceof Error ? e.message : e}`, "err");
    } finally {
      set((s) => ({ loading: s.loading - 1 }));
    }
  },

  setView: (v) => {
    set({ viewMode: v });
    syncUrl(get());
    if (v === "kg" && !get().kg) void get().loadKg();
  },

  toggleCluster: (c) =>
    set((s) => ({ isolateCluster: s.isolateCluster === c ? null : c })),

  selectPaper: (pid) => {
    set((s) => ({
      // Clicking a paper from the 3D concept view jumps back to the timeline.
      viewMode: s.viewMode === "kg" ? "timeline" : s.viewMode,
      selectedPaper: pid,
      selectedEntity: null,
    }));
    syncUrl(get());
  },

  showEntity: (eid) => {
    set({ selectedEntity: eid, selectedPaper: null });
    syncUrl(get());
  },

  closePanel: () => {
    set({ selectedPaper: null, selectedEntity: null });
    syncUrl(get());
  },

  setFilters: (f) => set({ filters: f }),
  setSizeMode: (m) => set({ sizeMode: m }),
  setShowCitation: (on) => set({ showCitation: on }),
  setShowSimilar: (on) => set({ showSimilar: on }),

  setSim: (v) => {
    set({ sim: v });
    // Legacy behaviour: reload edges debounced while dragging the slider.
    window.clearTimeout(simTimer);
    simTimer = window.setTimeout(() => void get().load(), 500);
  },

  setHits: (h) => set({ hits: h }),

  toast: (msg, kind = "info") => {
    const id = ++toastSeq;
    set((s) => ({ toasts: [...s.toasts, { id, msg, kind }] }));
    window.setTimeout(
      () => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
      6000,
    );
  },

  openMarkdown: (pid) => set({ markdownFor: pid }),
  closeMarkdown: () => set({ markdownFor: null }),
}));
