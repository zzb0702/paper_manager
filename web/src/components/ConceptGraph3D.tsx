import ForceGraph3D, { type ForceGraphMethods, type NodeObject } from "react-force-graph-3d";
import { useEffect, useMemo, useRef, useState } from "react";
import SpriteText from "three-spritetext";
import { useApp } from "../store";
import type { KgNode } from "../types";

// Node payload after react-force-graph decorates it with x/y/z.
type FgNode = KgNode & { x?: number; y?: number; z?: number };
type FgLink = { source: number | FgNode; target: number | FgNode; relation: string };

const KG_COLOR: Record<string, string> = {
  method: "#4f9cf9",
  dataset: "#7dd87d",
  task: "#f5a623",
  concept: "#b07df9",
};
const KG_TYPES = ["method", "dataset", "task", "concept"];
const DIM = "#2a3745";

// radius derived from nodeVal with nodeRelSize=8 (volume-proportional spheres)
function radiusOf(n: KgNode) {
  return Math.cbrt(((n.n_chunks + 1) * 8 * 0.75) / Math.PI);
}

export default function ConceptGraph3D() {
  const kg = useApp((s) => s.kg);
  const showEntity = useApp((s) => s.showEntity);
  const selectedEntity = useApp((s) => s.selectedEntity);
  const fgRef = useRef<ForceGraphMethods | undefined>(undefined);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [hlNodes, setHlNodes] = useState<Set<number>>(new Set());
  const [hlLinks, setHlLinks] = useState<Set<object>>(new Set());
  const fitDone = useRef<string | null>(null);

  // rfg-3d falls back to window size when auto-detecting on a freshly-mounted
  // parent, which then overflows the grid column and breaks the whole layout;
  // size is driven explicitly from the wrapper instead.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [kg]);

  // Deep-ish copy: rfg mutates node/link objects in place; keep the store clean.
  const graphData = useMemo(() => {
    if (!kg) return { nodes: [] as FgNode[], links: [] as FgLink[] };
    return {
      nodes: kg.nodes.map((n) => ({ ...n })),
      links: kg.edges.map((e) => ({ source: e.src, target: e.dst, relation: e.relation })),
    };
  }, [kg]);

  const adjacency = useMemo(() => {
    const m = new Map<number, { nodes: Set<number>; links: Set<object> }>();
    (kg?.nodes || []).forEach((n) => m.set(n.id, { nodes: new Set([n.id]), links: new Set() }));
    graphData.links.forEach((l) => {
      const s = (l.source as FgNode).id ?? (l.source as number);
      const t = (l.target as FgNode).id ?? (l.target as number);
      m.get(s as number)?.nodes.add(t as number);
      m.get(s as number)?.links.add(l);
      m.get(t as number)?.nodes.add(s as number);
      m.get(t as number)?.links.add(l);
    });
    return m;
  }, [kg, graphData]);

  // Fit camera once per dataset after the layout has mostly settled.
  useEffect(() => {
    if (!kg || !kg.nodes.length) return;
    const key = String(kg.nodes.length) + ":" + String(kg.edges.length);
    if (fitDone.current === key) return;
    fitDone.current = key;
    const t = window.setTimeout(() => fgRef.current?.zoomToFit(600, 120), 1800);
    return () => window.clearTimeout(t);
  }, [kg]);

  const active = hlNodes.size > 0;

  if (!kg || !kg.nodes.length) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2.5 text-dim">
        <div className="text-[40px]">🕸️</div>
        <div>
          概念图为空 — 先运行 <b>python cli.py build-kg --all</b> 从论文中抽取实体与关系
        </div>
      </div>
    );
  }

  return (
    <div ref={wrapRef} className="relative h-full w-full overflow-hidden">
      <ForceGraph3D
        ref={fgRef as never}
        graphData={graphData}
        width={size.w}
        height={size.h}
        backgroundColor="#0f1419"
        nodeVal={(n) => (n as KgNode).n_chunks + 1}
        nodeRelSize={8}
        nodeColor={(n) => {
          const node = n as KgNode;
          const base = KG_COLOR[node.type] || "#9aa7b5";
          if (node.id === selectedEntity) return "#ffffff";
          return active && !hlNodes.has(node.id) ? DIM : base;
        }}
        nodeLabel={(n) => {
          const node = n as KgNode;
          const desc = node.desc ? `<br>${node.desc}` : "";
          return `<b>${node.name}</b>（${node.type}，${node.n_chunks} 块）${desc}`;
        }}
        nodeThreeObject={(n: NodeObject) => {
          const node = n as KgNode;
          const name = node.name.length > 30 ? node.name.slice(0, 29) + "…" : node.name;
          const sprite = new SpriteText(name);
          sprite.color = "#aeb9c4";
          sprite.textHeight = 2.2;
          // three ships no typings, so SpriteText's base members resolve as errors
          // here — position exists at runtime (Object3D) and is set via a cast.
          (sprite as unknown as { position: { y: number } }).position.y = -(radiusOf(node) + 2.8);
          return sprite;
        }}
        nodeThreeObjectExtend
        linkColor={(l) =>
          active && hlLinks.has(l) ? "#4f9cf9" : "rgba(74,88,102,.75)"
        }
        linkWidth={(l) => (active && hlLinks.has(l) ? 1.8 : 0.6)}
        linkDirectionalArrowLength={3.2}
        linkDirectionalArrowColor={() => "#4a5866"}
        linkDirectionalArrowRelPos={1}
        linkLabel={(l) => (l as FgLink).relation}
        onNodeHover={(n) => {
          const node = n as KgNode | null;
          (document.body.style as unknown as { cursor?: string }).cursor = node ? "pointer" : "";
          if (!node) {
            setHlNodes(new Set());
            setHlLinks(new Set());
            return;
          }
          const adj = adjacency.get(node.id);
          setHlNodes(adj ? adj.nodes : new Set());
          setHlLinks(adj ? adj.links : new Set());
        }}
        onNodeClick={(n) => showEntity((n as KgNode).id)}
      />
      <div className="pointer-events-none absolute left-24 top-3 z-10 flex flex-col gap-1 text-xs text-ink">
        {KG_TYPES.map((t) => (
          <span key={t} className="flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: KG_COLOR[t] }} />
            {t}
          </span>
        ))}
      </div>
    </div>
  );
}
