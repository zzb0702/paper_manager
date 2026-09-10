import ForceGraph3D, {
  type ForceGraphMethods,
  type NodeObject,
} from "react-force-graph-3d";
import { useEffect, useMemo, useRef, useState } from "react";
import SpriteText from "three-spritetext";
import { useApp } from "../store";
import type { KgNode } from "../types";

type FgNode = KgNode & {
  x?: number;
  y?: number;
  z?: number;
  __dim?: boolean;
};
type FgLink = {
  source: number | FgNode;
  target: number | FgNode;
  relation: string;
  paper_id: number | null;
  __keep?: boolean;
};

const KG_COLOR: Record<string, string> = {
  method: "#5b9dff",
  dataset: "#3ecf8e",
  task: "#f0b429",
  concept: "#b07df9",
};
const KG_TYPES = ["method", "dataset", "task", "concept"] as const;
const DIM = "#1c2733";
const BG = "#0b0f14";

const REL_SIZE = 6.5;
function nodeValOf(n: KgNode) {
  return 1.1 + Math.min(n.n_chunks, 28) / 5;
}
function radiusOf(n: KgNode) {
  return REL_SIZE * Math.cbrt((nodeValOf(n) * 0.75) / Math.PI);
}

function fitCamera(
  fg: ForceGraphMethods | undefined,
  factor: number,
  ms: number,
  focusId?: number,
) {
  if (!fg) return;
  const g = (
    fg as unknown as { graphData(): { nodes: FgNode[] } }
  ).graphData();
  const nodes = g?.nodes ?? [];
  if (!nodes.length) return;

  let cx = 0,
    cy = 0,
    cz = 0;
  let target = nodes;
  if (focusId != null) {
    const hit = nodes.find((n) => n.id === focusId);
    if (hit) target = [hit];
  }
  for (const n of target) {
    cx += n.x ?? 0;
    cy += n.y ?? 0;
    cz += n.z ?? 0;
  }
  cx /= target.length;
  cy /= target.length;
  cz /= target.length;

  let r = 40;
  if (focusId == null) {
    for (const n of nodes) {
      r = Math.max(
        r,
        Math.hypot((n.x ?? 0) - cx, (n.y ?? 0) - cy, (n.z ?? 0) - cz),
      );
    }
  } else {
    // keep a little surrounding context when focusing one entity
    r = 55;
  }

  const p = fg.camera().position;
  const dx = p.x - cx,
    dy = p.y - cy,
    dz = p.z - cz;
  const len = Math.hypot(dx, dy, dz) || 1;
  // keep a stable viewing direction; if nearly degenerate look from +Z
  const ux = len < 1 ? 0 : dx / len;
  const uy = len < 1 ? 0 : dy / len;
  const uz = len < 1 ? 1 : dz / len;
  const dist = r * factor + 40;
  fg.cameraPosition(
    { x: cx + ux * dist, y: cy + uy * dist, z: cz + uz * dist },
    { x: cx, y: cy, z: cz },
    ms,
  );
}

function shortName(name: string, max = 28) {
  return name.length > max ? name.slice(0, max - 1) + "…" : name;
}

export default function ConceptGraph3D() {
  const kg = useApp((s) => s.kg);
  const byId = useApp((s) => s.byId);
  const showEntity = useApp((s) => s.showEntity);
  const selectedEntity = useApp((s) => s.selectedEntity);
  const closePanel = useApp((s) => s.closePanel);

  const fgRef = useRef<ForceGraphMethods | undefined>(undefined);
  const wrapRef = useRef<HTMLDivElement>(null);
  const spriteMap = useRef(new Map<number, SpriteText>());
  const fitDone = useRef<string | null>(null);

  const [size, setSize] = useState({ w: 0, h: 0 });
  const [hlNodes, setHlNodes] = useState<Set<number>>(new Set());
  const [hlLinks, setHlLinks] = useState<Set<object>>(new Set());
  const [query, setQuery] = useState("");
  const [typeOn, setTypeOn] = useState<Set<string>>(
    () => new Set(KG_TYPES as unknown as string[]),
  );
  const [paperFilter, setPaperFilter] = useState<number | "">("");
  const [showMinor, setShowMinor] = useState(false);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [kg]);

  // Deep copy for rfg mutation
  const baseGraph = useMemo(() => {
    if (!kg) return { nodes: [] as FgNode[], links: [] as FgLink[] };
    return {
      nodes: kg.nodes.map((n) => ({ ...n })),
      links: kg.edges.map((e) => ({
        source: e.src,
        target: e.dst,
        relation: e.relation,
        paper_id: e.paper_id,
      })),
    };
  }, [kg]);

  const paperOptions = useMemo(() => {
    const set = new Set<number>();
    (kg?.nodes || []).forEach((n) => n.paper_ids?.forEach((p) => set.add(p)));
    return [...set].sort((a, b) => a - b);
  }, [kg]);

  const degree = useMemo(() => {
    const m = new Map<number, number>();
    (kg?.nodes || []).forEach((n) => m.set(n.id, 0));
    (kg?.edges || []).forEach((e) => {
      m.set(e.src, (m.get(e.src) ?? 0) + 1);
      m.set(e.dst, (m.get(e.dst) ?? 0) + 1);
    });
    return m;
  }, [kg]);

  const q = query.trim().toLowerCase();
  const matchIds = useMemo(() => {
    if (!q) return null as Set<number> | null;
    const s = new Set<number>();
    for (const n of kg?.nodes || []) {
      if (
        n.name.toLowerCase().includes(q) ||
        (n.desc || "").toLowerCase().includes(q) ||
        n.type.toLowerCase() === q
      ) {
        s.add(n.id);
      }
    }
    return s;
  }, [kg, q]);

  // Visibility + adjacency on the filtered subgraph
  const { nodes: visNodes, links: visLinks, adjacency } = useMemo(() => {
    const all = baseGraph.nodes;
    const nodes: FgNode[] = [];
    const keep = new Set<number>();
    for (const n of all) {
      if (!typeOn.has(n.type)) continue;
      if (paperFilter !== "" && !(n.paper_ids || []).includes(paperFilter))
        continue;
      const deg = degree.get(n.id) ?? 0;
      if (!showMinor && n.n_chunks < 2 && deg <= 1) continue;
      nodes.push(n);
      keep.add(n.id);
    }
    const links: FgLink[] = baseGraph.links.filter((l) => {
      const s =
        typeof l.source === "object" ? (l.source as FgNode).id : l.source;
      const t =
        typeof l.target === "object" ? (l.target as FgNode).id : l.target;
      return keep.has(s as number) && keep.has(t as number);
    });
    const adj = new Map<number, { nodes: Set<number>; links: Set<object> }>();
    nodes.forEach((n) => adj.set(n.id, { nodes: new Set([n.id]), links: new Set() }));
    links.forEach((l) => {
      const s =
        ((l.source as FgNode).id ?? (l.source as number)) as number;
      const t =
        ((l.target as FgNode).id ?? (l.target as number)) as number;
      adj.get(s)?.nodes.add(t);
      adj.get(s)?.links.add(l);
      adj.get(t)?.nodes.add(s);
      adj.get(t)?.links.add(l);
    });
    return { nodes, links, adjacency: adj };
  }, [baseGraph, typeOn, paperFilter, showMinor, degree]);

  // Fit once per filtered graph identity
  useEffect(() => {
    if (!visNodes.length) return;
    const key = `${visNodes.length}:${visLinks.length}:${showMinor}:${paperFilter}`;
    fitDone.current = key;
    const early = window.setTimeout(() => fitCamera(fgRef.current, 2.4, 600), 2000);
    return () => window.clearTimeout(early);
  }, [visNodes, visLinks, showMinor, paperFilter]);

  const fitOnEngineStop = () => {
    const key = `${visNodes.length}:${visLinks.length}:${showMinor}:${paperFilter}`;
    if (fitDone.current !== key) return;
    fitDone.current = null;
    fitCamera(fgRef.current, 2.5, 700);
  };

  // Cap DPR
  useEffect(() => {
    const fg = fgRef.current as unknown as
      | { renderer?: () => { setPixelRatio: (v: number) => void } }
      | undefined;
    fg?.renderer?.().setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  }, [kg, size.w, size.h, visNodes.length]);

  // Focus camera when selection or search pick changes
  const focusRef = useRef<number | null>(null);
  useEffect(() => {
    if (selectedEntity != null && selectedEntity !== focusRef.current) {
      focusRef.current = selectedEntity;
      const t = window.setTimeout(
        () => fitCamera(fgRef.current, 1.6, 500, selectedEntity),
        80,
      );
      return () => window.clearTimeout(t);
    }
    if (selectedEntity == null) focusRef.current = null;
  }, [selectedEntity]);

  // Labels: always for n_chunks>=2; also for query matches / selected / neighbors
  const labelOn = (id: number) => {
    const n = (kg?.nodes || []).find((x) => x.id === id);
    if (!n) return false;
    if (n.n_chunks >= 2) return true;
    if (selectedEntity === id) return true;
    if (matchIds?.has(id)) return true;
    if (selectedEntity != null && hlNodes.has(id)) return true;
    return false;
  };

  const active = hlNodes.size > 0 || (matchIds != null && matchIds.size > 0);
  const searchActive = matchIds != null && matchIds.size > 0;
  const searchMiss = q.length > 0 && matchIds != null && matchIds.size === 0;

  if (!kg || !kg.nodes.length) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2.5 text-dim">
        <div className="empty-orb" />
        <div className="text-[13px] text-ink/70">概念图还是空的</div>
        <div className="text-[12px]">
          先运行 <b className="text-ink">python cli.py build-kg --all</b>
        </div>
      </div>
    );
  }

  const matchList = matchIds
    ? (kg.nodes.filter((n) => matchIds.has(n.id)).slice(0, 8))
    : [];

  return (
    <div ref={wrapRef} className="relative h-full w-full overflow-hidden">
      <ForceGraph3D
        ref={fgRef as never}
        graphData={{ nodes: visNodes, links: visLinks }}
        width={size.w}
        height={size.h}
        backgroundColor={BG}
        rendererConfig={{ antialias: false, powerPreference: "high-performance" }}
        nodeVal={(n) => nodeValOf(n as KgNode)}
        nodeRelSize={REL_SIZE}
        nodeOpacity={0.92}
        linkOpacity={0.55}
        warmupTicks={40}
        cooldownTime={3500}
        d3VelocityDecay={0.35}
        nodeColor={(n) => {
          const node = n as KgNode;
          if (node.id === selectedEntity) return "#ffffff";
          if (searchActive && matchIds?.has(node.id))
            return KG_COLOR[node.type] || "#9aa7b5";
          if (active && !hlNodes.has(node.id)) return DIM;
          return KG_COLOR[node.type] || "#9aa7b5";
        }}
        nodeLabel={(n) => {
          const node = n as KgNode;
          const papers = (node.paper_ids || [])
            .map((pid) => {
              const p = byId.get(pid);
              return p ? `[${pid}] ${p.title.slice(0, 28)}` : `#${pid}`;
            })
            .join("<br>");
          const desc = node.desc ? `<br><span style="opacity:.85">${node.desc}</span>` : "";
          return (
            `<div style="max-width:280px;font:12px/1.4 system-ui,sans-serif">` +
            `<b style="font-size:13px">${node.name}</b><br>` +
            `<span style="color:#8aa0b4">${node.type} · ${node.n_chunks} 块</span>` +
            (papers ? `<br><span style="color:#8aa0b4">${papers}</span>` : "") +
            desc +
            `</div>`
          );
        }}
        nodeThreeObject={(n: NodeObject) => {
          const node = n as KgNode;
          const show =
            labelOn(node.id) ||
            (searchActive && matchIds?.has(node.id)) ||
            node.id === selectedEntity;
          if (!show) return null;
          const sprite = new SpriteText(shortName(node.name));
          const isHot =
            node.id === selectedEntity ||
            (searchActive && matchIds?.has(node.id)) ||
            hlNodes.has(node.id);
          sprite.color = isHot ? "#f2f7fc" : "#9aabb8";
          sprite.fontFace = "Segoe UI, system-ui, sans-serif";
          sprite.textHeight = isHot ? 3.2 : 2.6;
          sprite.backgroundColor = "rgba(10,14,18,0.55)";
          sprite.padding = 1.2;
          sprite.borderRadius = 2;
          spriteMap.current.set(node.id, sprite);
          (sprite as unknown as { position: { y: number } }).position.y =
            -(radiusOf(node) + 3.2);
          return sprite;
        }}
        nodeThreeObjectExtend
        linkColor={(l) => {
          if (active && (hlLinks.has(l) || (searchActive && matchIds?.has(
            typeof (l as FgLink).source === "object"
              ? ((l as FgLink).source as FgNode).id
              : ((l as FgLink).source as number),
          )))) {
            return "#dce8f4";
          }
          if (active) return DIM;
          // subtle neutral lines — rainbow-by-paper is unreadable at this density
          return "rgba(110,130,150,0.35)";
        }}
        linkWidth={(l) => (active && hlLinks.has(l) ? 2.2 : 0.9)}
        linkLabel={(l) => {
          const fl = l as FgLink;
          const p = fl.paper_id != null ? byId.get(fl.paper_id) : undefined;
          const src = p ? `<br><span style="color:#8aa0b4">[${p.id}] ${p.title.slice(0, 40)}</span>` : "";
          return `<div style="font:12px system-ui"><b>${fl.relation}</b>${src}</div>`;
        }}
        onNodeHover={(n) => {
          const node = n as KgNode | null;
          (document.body.style as unknown as { cursor?: string }).cursor = node
            ? "pointer"
            : "";
          if (!node) {
            // keep selection highlight; clear pure hover
            if (selectedEntity == null) {
              setHlNodes(new Set());
              setHlLinks(new Set());
            } else {
              const adj = adjacency.get(selectedEntity);
              setHlNodes(adj ? adj.nodes : new Set([selectedEntity]));
              setHlLinks(adj ? adj.links : new Set());
            }
            spriteMap.current.forEach((sp, id) => {
              (sp as unknown as { visible: boolean }).visible = labelOn(id);
            });
            return;
          }
          const adj = adjacency.get(node.id);
          setHlNodes(adj ? adj.nodes : new Set());
          setHlLinks(adj ? adj.links : new Set());
          spriteMap.current.forEach((sp, id) => {
            (sp as unknown as { visible: boolean }).visible =
              labelOn(id) || (adj?.nodes.has(id) ?? false);
          });
        }}
        onNodeClick={(n) => showEntity((n as KgNode).id)}
        onBackgroundClick={() => {
          if (selectedEntity != null) closePanel();
        }}
        onEngineStop={fitOnEngineStop}
      />

      {/* Control bar */}
      <div className="pointer-events-auto absolute top-11 left-3 z-10 w-[280px] rounded-lg border border-line bg-panel/95 p-2.5 shadow-xl backdrop-blur-md">
        <label className="mb-1.5 block text-[10.5px] tracking-wide text-dim">
          搜索实体
        </label>
        <div className="mb-2 flex gap-1.5">
          <input
            className="input min-w-0 flex-1"
            placeholder="实体名 / 描述 / 类型…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && matchList[0]) {
                showEntity(matchList[0].id);
              }
            }}
          />
          <button
            className="btn btn-ghost"
            title="清除搜索"
            onClick={() => setQuery("")}
            disabled={!query}
          >
            ✕
          </button>
        </div>
        {q && (
          <div className="mb-2 max-h-[140px] overflow-y-auto rounded-md border border-line bg-field/60">
            {matchList.length === 0 ? (
              <div className="px-2 py-1.5 text-[11px] text-dim">无匹配实体</div>
            ) : (
              matchList.map((n) => (
                <button
                  key={n.id}
                  className="flex w-full items-center gap-1.5 px-2 py-1 text-left text-[11.5px] hover:bg-raise"
                  onClick={() => showEntity(n.id)}
                >
                  <span
                    className="h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{ background: KG_COLOR[n.type] || "#9aa7b5" }}
                  />
                  <span className="truncate text-ink">{n.name}</span>
                  <span className="ml-auto shrink-0 text-dim tabular-nums">
                    {n.n_chunks}
                  </span>
                </button>
              ))
            )}
          </div>
        )}

        <label className="mb-1 block text-[10.5px] tracking-wide text-dim">
          实体类型
        </label>
        <div className="mb-2 flex flex-wrap gap-1">
          {KG_TYPES.map((t) => {
            const on = typeOn.has(t);
            return (
              <button
                key={t}
                className={`chip ${on ? "chip-on" : ""}`}
                onClick={() => {
                  setTypeOn((prev) => {
                    const next = new Set(prev);
                    if (next.has(t)) next.delete(t);
                    else next.add(t);
                    // never allow empty — keep last type if user unchecks all
                    if (next.size === 0) return prev;
                    return next;
                  });
                }}
              >
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ background: KG_COLOR[t], opacity: on ? 1 : 0.35 }}
                />
                {t}
              </button>
            );
          })}
        </div>

        {paperOptions.length > 1 && (
          <>
            <label className="mb-1 block text-[10.5px] tracking-wide text-dim">
              来源论文
            </label>
            <select
              className="input mb-2 w-full"
              value={paperFilter === "" ? "" : String(paperFilter)}
              onChange={(e) =>
                setPaperFilter(
                  e.target.value === "" ? "" : Number(e.target.value),
                )
              }
            >
              <option value="">全部论文</option>
              {paperOptions.map((pid) => {
                const p = byId.get(pid);
                return (
                  <option key={pid} value={pid}>
                    [{pid}] {p ? p.title.slice(0, 36) : ""}
                  </option>
                );
              })}
            </select>
          </>
        )}

        <label className="mini-label flex cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            checked={showMinor}
            onChange={(e) => setShowMinor(e.target.checked)}
          />
          显示次要实体（仅 1 块且孤立）
        </label>

        <div className="mt-2 flex items-center justify-between border-t border-line pt-2 text-[10px] text-dim">
          <span className="tabular-nums">
            {visNodes.length} 节点 · {visLinks.length} 边
            {searchMiss ? " · 无匹配" : ""}
            {searchActive ? ` · 命中 ${matchIds!.size}` : ""}
          </span>
          <button
            className="text-accent hover:underline"
            onClick={() => fitCamera(fgRef.current, 2.5, 500)}
          >
            适配视野
          </button>
        </div>
      </div>

      {/* Compact type legend only (paper list moved out — too tall) */}
      <div className="pointer-events-none absolute right-3 top-11 z-10 rounded-lg border border-line bg-panel/90 px-2.5 py-1.5 text-[10px] text-dim shadow-lg backdrop-blur-sm">
        <div className="mb-1">左键旋转 · 滚轮缩放 · 右键平移</div>
        <div className="mb-1">悬停高亮邻域 · 点击看详情</div>
        <div className="flex flex-wrap gap-x-2 gap-y-0.5">
          {KG_TYPES.map((t) => (
            <span key={t} className="inline-flex items-center gap-1">
              <span
                className="inline-block h-1.5 w-1.5 rounded-full"
                style={{ background: KG_COLOR[t] }}
              />
              {t}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
