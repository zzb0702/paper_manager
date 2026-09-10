import * as echarts from "echarts";
import { useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "../store";
import type { KgNode } from "../types";

const esc = (s: string | null | undefined) =>
  (s || "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] as string);

const KG_COLOR: Record<string, string> = {
  method: "#5b9dff",
  dataset: "#3ecf8e",
  task: "#f0b429",
  concept: "#b07df9",
};
const KG_TYPES = ["method", "dataset", "task", "concept"] as const;

function shortName(name: string, max = 22) {
  return name.length > max ? name.slice(0, max - 1) + "…" : name;
}

export default function ConceptGraph2D() {
  const kg = useApp((s) => s.kg);
  const byId = useApp((s) => s.byId);
  const showEntity = useApp((s) => s.showEntity);
  const selectedEntity = useApp((s) => s.selectedEntity);

  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const prevKgRef = useRef<unknown>(null);

  const [query, setQuery] = useState("");
  const [typeOn, setTypeOn] = useState<Set<string>>(
    () => new Set(KG_TYPES as unknown as string[]),
  );
  const [paperFilter, setPaperFilter] = useState<number | "">("");
  const [showMinor, setShowMinor] = useState(false);

  const degree = useMemo(() => {
    const m = new Map<number, number>();
    (kg?.nodes || []).forEach((n) => m.set(n.id, 0));
    (kg?.edges || []).forEach((e) => {
      m.set(e.src, (m.get(e.src) ?? 0) + 1);
      m.set(e.dst, (m.get(e.dst) ?? 0) + 1);
    });
    return m;
  }, [kg]);

  const paperOptions = useMemo(() => {
    const set = new Set<number>();
    (kg?.nodes || []).forEach((n) => n.paper_ids?.forEach((p) => set.add(p)));
    return [...set].sort((a, b) => a - b);
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

  const visibleNodes = useMemo(() => {
    if (!kg) return [] as KgNode[];
    return kg.nodes.filter((n) => {
      if (!typeOn.has(n.type)) return false;
      if (paperFilter !== "" && !(n.paper_ids || []).includes(paperFilter))
        return false;
      if (!showMinor && n.n_chunks < 2 && (degree.get(n.id) ?? 0) <= 1)
        return false;
      return true;
    });
  }, [kg, typeOn, paperFilter, showMinor, degree]);

  const visibleIds = useMemo(
    () => new Set(visibleNodes.map((n) => n.id)),
    [visibleNodes],
  );

  const matchList = useMemo(() => {
    if (!matchIds) return [];
    return (kg?.nodes || []).filter((n) => matchIds.has(n.id)).slice(0, 8);
  }, [kg, matchIds]);

  // Always mount the chart host — early-returning before init leaves
  // hostRef unattached and ECharts never starts (blank concept view).
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const chart = echarts.init(host);
    chartRef.current = chart;
    chart.on("click", (params) => {
      const p = params as unknown as {
        dataType?: string;
        data?: { id?: string | number } | null;
      };
      if (p.dataType === "node" && p.data?.id != null) {
        useApp.getState().showEntity(Number(p.data.id));
      }
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(host);
    return () => {
      ro.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  // Re-init when the host appears after first paint (kg still loading).
  useEffect(() => {
    if (!hostRef.current || chartRef.current) return;
    const t = window.setTimeout(() => {
      if (hostRef.current && !chartRef.current) {
        chartRef.current = echarts.init(hostRef.current);
      }
    }, 0);
    return () => window.clearTimeout(t);
  }, [kg]);

  useEffect(() => {
    let chart = chartRef.current;
    if (!chart && hostRef.current) {
      chart = echarts.init(hostRef.current);
      chartRef.current = chart;
    }
    if (!chart) return;

    if (!kg || !kg.nodes.length) {
      chart.clear();
      return;
    }

    const nodes = visibleNodes.map((n) => {
      const isMatch = matchIds?.has(n.id) ?? false;
      const isSel = selectedEntity === n.id;
      const showLabel =
        n.n_chunks >= 3 || isMatch || isSel || (q.length > 0 && isMatch);
      return {
        id: String(n.id),
        name: shortName(n.name),
        symbolSize: 8 + Math.min(n.n_chunks, 24) * 0.9,
        category: Math.max(0, KG_TYPES.indexOf(n.type as (typeof KG_TYPES)[number])),
        entity: n,
        itemStyle: {
          color: isSel ? "#ffffff" : KG_COLOR[n.type] || "#9aa7b5",
          borderColor: isSel ? "#5b9dff" : "transparent",
          borderWidth: isSel ? 3 : 0,
          opacity: matchIds && !isMatch && !isSel ? 0.18 : 0.95,
        },
        label: {
          show: showLabel,
          color: isSel || isMatch ? "#f2f7fc" : "#9aabb8",
          fontSize: isSel || isMatch ? 12 : 11,
        },
      };
    });

    const links = (kg?.edges || [])
      .filter((e) => visibleIds.has(e.src) && visibleIds.has(e.dst))
      .map((e) => {
        const hot =
          selectedEntity != null &&
          (e.src === selectedEntity || e.dst === selectedEntity);
        const paper =
          e.paper_id != null ? byId.get(e.paper_id) : undefined;
        return {
          source: String(e.src),
          target: String(e.dst),
          relation: e.relation,
          paperTitle: paper ? paper.title : "",
          paperId: e.paper_id,
          lineStyle: {
            color: hot ? "#dce8f4" : "rgba(110,130,150,0.45)",
            width: hot ? 2.2 : 0.9,
            opacity: matchIds && !hot ? 0.08 : 0.85,
            curveness: 0.08,
          },
        };
      });

    const categories = KG_TYPES.map((t) => ({
      name: t,
      itemStyle: { color: KG_COLOR[t] },
    }));

    chart.setOption(
      {
        backgroundColor: "transparent",
        tooltip: {
          trigger: "item",
          confine: true,
          backgroundColor: "rgba(18,24,32,0.96)",
          borderColor: "#1e2833",
          textStyle: { color: "#e4ecf4", fontSize: 12 },
          formatter: (p: {
            dataType?: string;
            data?: {
              entity?: KgNode;
              relation?: string;
              paperTitle?: string;
              paperId?: number | null;
            };
          }) => {
            if (p.dataType === "node") {
              const n = p.data?.entity;
              if (!n) return "";
              const papers = (n.paper_ids || [])
                .map((pid) => {
                  const paper = byId.get(pid);
                  return paper ? `[${pid}] ${esc(paper.title.slice(0, 36))}` : `#${pid}`;
                })
                .join("<br>");
              return (
                `<b style="font-size:13px">${esc(n.name)}</b><br>` +
                `<span style="color:#8aa0b4">${esc(n.type)} · ${n.n_chunks} 块</span>` +
                (papers ? `<br><span style="color:#8aa0b4">${papers}</span>` : "") +
                (n.desc ? `<br><span style="color:#a8b6c4">${esc(n.desc)}</span>` : "")
              );
            }
            if (p.dataType === "edge") {
              const d = p.data;
              const src = d?.paperTitle
                ? `<br><span style="color:#8aa0b4">[${d.paperId}] ${esc(d.paperTitle.slice(0, 40))}</span>`
                : "";
              return `<b>${esc(d?.relation || "")}</b>${src}`;
            }
            return "";
          },
        },
        series: [
          {
            id: "kg",
            type: "graph",
            layout: "force",
            roam: true,
            draggable: true,
            data: nodes,
            links,
            categories,
            label: {
              show: true,
              position: "bottom",
              fontSize: 11,
              color: "#9aabb8",
            },
            labelLayout: { hideOverlap: true },
            emphasis: {
              focus: "adjacency",
              lineStyle: { width: 2.5, opacity: 1 },
              label: { show: true },
            },
            blur: { itemStyle: { opacity: 0.12 }, lineStyle: { opacity: 0.05 } },
            force: {
              repulsion: 280,
              edgeLength: [50, 140],
              gravity: 0.18,
              friction: 0.6,
              layoutAnimation: true,
            },
          },
        ],
      },
      // First paint / dataset swap needs a full rebuild; filter tweaks can merge.
      { notMerge: prevKgRef.current !== kg },
    );
    prevKgRef.current = kg;
    chart.resize();

    if (selectedEntity != null && visibleIds.has(selectedEntity)) {
      const idx = nodes.findIndex((n) => Number(n.id) === selectedEntity);
      if (idx >= 0) {
        chart.dispatchAction({ type: "select", seriesIndex: 0, dataIndex: idx });
        chart.dispatchAction({
          type: "focusNodeAdjacency",
          seriesIndex: 0,
          dataIndex: idx,
        });
      }
    }
  }, [
    kg,
    byId,
    visibleNodes,
    visibleIds,
    matchIds,
    selectedEntity,
    q,
    showMinor,
    paperFilter,
    typeOn,
  ]);

  const empty = !kg || !kg.nodes.length;

  return (
    <div className="relative h-full w-full">
      <div ref={hostRef} className="h-full w-full" />

      {empty && (
        <div className="pointer-events-none absolute inset-0 z-[5] flex flex-col items-center justify-center gap-2.5 text-dim">
          <div className="empty-orb" />
          <div className="text-[13px] text-ink/70">概念图还是空的</div>
          <div className="text-[12px]">
            先运行 <b className="text-ink">python cli.py build-kg --all</b>
          </div>
        </div>
      )}

      <div className="pointer-events-auto absolute top-11 left-3 z-10 w-[270px] rounded-lg border border-line bg-panel/95 p-2.5 shadow-xl backdrop-blur-md">
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
              if (e.key === "Enter" && matchList[0]) showEntity(matchList[0].id);
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
          {KG_TYPES.map((t) => (
            <button
              key={t}
              className={`chip ${typeOn.has(t) ? "chip-on" : ""}`}
              onClick={() => {
                setTypeOn((prev) => {
                  const next = new Set(prev);
                  if (next.has(t)) next.delete(t);
                  else next.add(t);
                  if (next.size === 0) return prev;
                  return next;
                });
              }}
            >
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{
                  background: KG_COLOR[t],
                  opacity: typeOn.has(t) ? 1 : 0.35,
                }}
              />
              {t}
            </button>
          ))}
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

        <div className="mt-2 border-t border-line pt-2 text-[10px] text-dim">
          点击实体看详情 · 拖拽平移 · 滚轮缩放
          <div className="mt-0.5 tabular-nums">
            {visibleNodes.length} 节点
            {matchIds ? ` · 命中 ${matchIds.size}` : ""}
          </div>
        </div>
      </div>
    </div>
  );
}
