import * as echarts from "echarts";
import { useEffect, useRef } from "react";
import { useApp } from "../store";
import { PALETTE } from "../palette";
import type { Filters, GraphData, PaperNode, SizeMode } from "../types";

const esc = (s: string | null | undefined) =>
  (s || "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] as string);

function matchFilters(n: PaperNode, filters: Filters | null): boolean {
  if (!filters) return true;
  if (filters.yearMin != null && (n.year || 0) < filters.yearMin) return false;
  if (filters.yearMax != null && (n.year || 9999) > filters.yearMax) return false;
  if (filters.author && !(n.authors || "").toLowerCase().includes(filters.author)) return false;
  if (filters.venue && !(n.venue || "").toLowerCase().includes(filters.venue)) return false;
  return true;
}

function nodeVisible(n: PaperNode, filters: Filters | null, isolate: number | null) {
  if (isolate != null && n.cluster !== isolate) return false;
  return matchFilters(n, filters);
}

function nodeSize(n: PaperNode, mode: SizeMode) {
  if (mode === "cited") return 9 + Math.min(Math.sqrt(n.cited_by_count || 0) * 2.2, 24);
  if (mode === "chunks") return 9 + Math.min(n.n_chunks || 1, 60) / 3;
  return 11;
}

// Timeline layout: x = year, lanes = citation connected components sorted by
// earliest year, small x/y offsets inside a (lane, year) cell. (ported verbatim)
function computeCoords(data: GraphData): Record<number, { x: number; y: number }> {
  const nodes = data.nodes;
  const years = nodes.map((n) => n.year || 2024);
  let minY = Math.min(...years);
  let maxY = Math.max(...years);
  if (maxY - minY < 2) {
    minY -= 1;
    maxY += 1;
  }
  const span = Math.max(maxY - minY, 1);
  const step = 240 / span;

  const parent: Record<number, number> = {};
  nodes.forEach((n) => (parent[n.id] = n.id));
  const find = (x: number): number => {
    while (parent[x] !== x) {
      parent[x] = parent[parent[x]];
      x = parent[x];
    }
    return x;
  };
  data.edges
    .filter((e) => e.kind === "citation")
    .forEach((e) => {
      if (parent[e.src] != null && parent[e.dst] != null) {
        const a = find(e.src);
        const b = find(e.dst);
        if (a !== b) parent[a] = b;
      }
    });
  const comps: Record<number, PaperNode[]> = {};
  nodes.forEach((n) => {
    const r = find(n.id);
    (comps[r] = comps[r] || []).push(n);
  });
  const ordered = Object.values(comps).sort(
    (a, b) =>
      Math.min(...a.map((n) => n.year || 2024)) - Math.min(...b.map((n) => n.year || 2024)),
  );
  const xy: Record<number, { x: number; y: number }> = {};
  const groupCount: Record<string, number> = {};
  ordered.forEach((comp, lane) => {
    comp.sort((a, b) => (a.year || 2024) - (b.year || 2024) || a.id - b.id);
    comp.forEach((n, k) => {
      const year = n.year || 2024;
      const key = lane + ":" + year;
      const i = (groupCount[key] = groupCount[key] || 0);
      groupCount[key]++;
      xy[n.id] = { x: (year - minY) * step + i * 26, y: -lane * 110 - k * 16 };
    });
  });
  return xy;
}

export default function PaperGraph2D() {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const prevRef = useRef<{ graph: GraphData; view: string }>({
    graph: useApp.getState().graph,
    view: useApp.getState().viewMode,
  });

  const graph = useApp((s) => s.graph);
  const viewMode = useApp((s) => s.viewMode);
  const filters = useApp((s) => s.filters);
  const isolateCluster = useApp((s) => s.isolateCluster);
  const sizeMode = useApp((s) => s.sizeMode);
  const showCitation = useApp((s) => s.showCitation);
  const showSimilar = useApp((s) => s.showSimilar);
  const selectedPaper = useApp((s) => s.selectedPaper);

  // init / dispose + one-time click wiring
  useEffect(() => {
    const chart = echarts.init(hostRef.current!);
    chartRef.current = chart;
    chart.on("click", (params) => {
      const p = params as unknown as { dataType?: string; data?: { id?: string | number } | null };
      if (p.dataType === "node" && p.data?.id != null) {
        useApp.getState().selectPaper(Number(p.data.id));
      }
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(hostRef.current!);
    return () => {
      ro.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const st = useApp.getState();

    const reset = prevRef.current.graph !== graph || prevRef.current.view !== viewMode;
    prevRef.current = { graph, view: viewMode };

    const xy = viewMode === "timeline" ? computeCoords(graph) : null;
    const counts: Record<number, number> = {};
    graph.nodes.forEach((n) => (counts[n.cluster] = (counts[n.cluster] || 0) + 1));
    const categories = Object.keys(counts)
      .sort((a, b) => +a - +b)
      .map((c) => ({
        name: `簇 ${+c + 1} · ${counts[+c]} 篇`,
        itemStyle: { color: PALETTE[+c % PALETTE.length] },
      }));

    const nodes = graph.nodes.map((n) => {
      const vis = nodeVisible(n, filters, isolateCluster);
      const d: Record<string, unknown> = {
        id: String(n.id),
        name: n.title.length > 36 ? n.title.slice(0, 35) + "…" : n.title,
        category: n.cluster,
        symbolSize: nodeSize(n, sizeMode),
        paper: n,
        itemStyle: { opacity: vis ? 1 : 0.08 },
        label: { show: vis },
      };
      if (viewMode === "timeline" && xy?.[n.id]) {
        d.x = xy[n.id].x;
        d.y = xy[n.id].y;
      }
      return d;
    });

    const links = graph.edges
      .filter((e) => {
        if (e.kind === "citation" && !showCitation) return false;
        if (e.kind === "similar" && !showSimilar) return false;
        const a = st.byId.get(e.src);
        const b = st.byId.get(e.dst);
        return !!a && !!b && nodeVisible(a, filters, isolateCluster) && nodeVisible(b, filters, isolateCluster);
      })
      .map((e) => {
        const sim = e.kind === "similar";
        return {
          source: String(e.src),
          target: String(e.dst),
          kind: e.kind,
          weight: e.weight || 0,
          lineStyle: {
            color: sim ? "#4a5866" : "#f5a623",
            width: sim ? 1 + ((e.weight || 0.55) - 0.55) * 8 : 1.6,
            type: sim ? "dashed" : "solid",
            opacity: 0.6,
            curveness: 0.12,
          },
          symbol: sim ? ["none", "none"] : ["none", "arrow"],
          symbolSize: 7,
        };
      });

    chart.setOption(
      {
        backgroundColor: "transparent",
        tooltip: {
          trigger: "item",
          formatter: (p: { dataType?: string; data?: Record<string, never> }) => {
            if (p.dataType === "node") {
              const n = useApp.getState().byId.get(Number((p.data as { id?: string }).id));
              if (!n) return "";
              return (
                `<b>${esc(n.title)}</b><br><span style="color:#7b8a99">` +
                `${esc(n.authors) || "佚名"} · ${n.year || "?"} · 被引 ${n.cited_by_count || 0}` +
                ` · ${n.n_chunks} 块</span>` +
                (n.summary ? `<br>${esc(n.summary.slice(0, 150))}…` : "")
              );
            }
            if (p.dataType === "edge") {
              const dd = p.data as { kind?: string; weight?: number };
              const kind =
                dd.kind === "citation" ? "引文关系" : `语义相似 ${dd.weight ?? ""}`;
              return `<span style="color:#7b8a99">${kind}</span>`;
            }
            return "";
          },
        },
        series: [
          {
            type: "graph",
            layout: viewMode === "force" ? "force" : "none",
            roam: true,
            data: nodes,
            links,
            categories,
            label: { show: true, position: "bottom", fontSize: 11, color: "#aeb9c4" },
            labelLayout: { hideOverlap: true },
            emphasis: { focus: "adjacency", lineStyle: { width: 3 } },
            blur: { itemStyle: { opacity: 0.1 }, lineStyle: { opacity: 0.04 } },
            selectedMode: "single",
            select: {
              itemStyle: {
                borderColor: "#fff",
                borderWidth: 2,
                shadowBlur: 14,
                shadowColor: "rgba(79,156,249,.8)",
              },
            },
            force: { repulsion: 420, edgeLength: [60, 160], gravity: 0.12 },
          },
        ],
      },
      reset,
    );

    if (selectedPaper != null && st.byId.has(selectedPaper)) {
      const idx = nodes.findIndex((n) => Number(n.id) === selectedPaper);
      if (idx >= 0) chart.dispatchAction({ type: "select", seriesIndex: 0, dataIndex: idx });
    }
  }, [
    graph, viewMode, filters, isolateCluster, sizeMode,
    showCitation, showSimilar, selectedPaper,
  ]);

  return <div ref={hostRef} className="h-full w-full" />;
}
