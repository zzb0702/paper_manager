import { useMemo, useState } from "react";
import { api } from "../api";
import { useApp } from "../store";
import { PALETTE } from "../palette";
import type { Filters, SizeMode } from "../types";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <h3 className="sect-title">{title}</h3>
      {children}
    </div>
  );
}

function SearchPanel() {
  const hits = useApp((s) => s.hits);
  const setHits = useApp((s) => s.setHits);
  const selectPaper = useApp((s) => s.selectPaper);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);

  async function go() {
    const query = q.trim();
    if (!query) {
      setHits(null);
      return;
    }
    setBusy(true);
    try {
      const { hits: h } = await api.search(query);
      setHits(h);
    } catch (e) {
      useApp.getState().toast(`检索失败: ${e instanceof Error ? e.message : e}`, "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="检索">
      <div className="mb-1.5 flex gap-1.5">
        <input
          className="input flex-1"
          placeholder="语义/关键词检索…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && go()}
        />
        <button className="btn" onClick={go}>搜</button>
      </div>
      {busy && <div className="card"><small className="text-dim">检索中…</small></div>}
      {!busy && hits && hits.length === 0 && (
        <div className="card"><small className="text-dim">没有找到相关论文</small></div>
      )}
      {!busy &&
        hits?.map((h, i) => (
          <div key={h.chunk_id + ":" + i} className="card" onClick={() => selectPaper(h.paper_id)}>
            <b className="block text-xs">[{h.paper_id}] {h.title}</b>
            <small className="text-dim">
              {h.year || "?"} · {h.section}{h.pages ? ` · p${h.pages}` : ""} · {h.matched_chunks} 处相关
            </small>
          </div>
        ))}
    </Section>
  );
}

function FilterPanel() {
  const setFilters = useApp((s) => s.setFilters);
  const [draft, setDraft] = useState({ y0: "", y1: "", author: "", venue: "" });

  function apply() {
    const f: Filters = {};
    if (draft.y0) f.yearMin = +draft.y0;
    if (draft.y1) f.yearMax = +draft.y1;
    if (draft.author.trim()) f.author = draft.author.trim().toLowerCase();
    if (draft.venue.trim()) f.venue = draft.venue.trim().toLowerCase();
    setFilters(Object.keys(f).length ? f : null);
  }

  return (
    <Section title="筛选">
      <div className="mb-1.5 flex gap-1.5">
        <input className="input flex-1" type="number" placeholder="年份≥"
          value={draft.y0} onChange={(e) => setDraft({ ...draft, y0: e.target.value })} />
        <input className="input flex-1" type="number" placeholder="年份≤"
          value={draft.y1} onChange={(e) => setDraft({ ...draft, y1: e.target.value })} />
      </div>
      <div className="mb-1.5 flex gap-1.5">
        <input className="input flex-1" placeholder="作者包含…"
          value={draft.author} onChange={(e) => setDraft({ ...draft, author: e.target.value })} />
      </div>
      <div className="mb-1.5 flex gap-1.5">
        <input className="input flex-1" placeholder="期刊/会议包含…"
          value={draft.venue} onChange={(e) => setDraft({ ...draft, venue: e.target.value })} />
      </div>
      <div className="flex gap-1.5">
        <button className="btn flex-1" onClick={apply}>应用筛选</button>
        <button className="btn" onClick={() => { setDraft({ y0: "", y1: "", author: "", venue: "" }); setFilters(null); }}>
          清除
        </button>
      </div>
    </Section>
  );
}

function AppearancePanel() {
  const sizeMode = useApp((s) => s.sizeMode);
  const setSizeMode = useApp((s) => s.setSizeMode);
  const showCitation = useApp((s) => s.showCitation);
  const setShowCitation = useApp((s) => s.setShowCitation);
  const showSimilar = useApp((s) => s.showSimilar);
  const setShowSimilar = useApp((s) => s.setShowSimilar);
  const sim = useApp((s) => s.sim);
  const setSim = useApp((s) => s.setSim);

  return (
    <Section title="外观">
      <label className="mini-label">节点大小</label>
      <div className="mb-1.5 flex gap-1.5">
        <select
          className="input flex-1"
          value={sizeMode}
          onChange={(e) => setSizeMode(e.target.value as SizeMode)}
        >
          <option value="cited">被引数</option>
          <option value="chunks">章节块数</option>
          <option value="uniform">统一大小</option>
        </select>
      </div>
      <label className="mini-label flex items-center gap-1.5">
        <input type="checkbox" checked={showCitation} onChange={(e) => setShowCitation(e.target.checked)} />
        引文边（实线箭头）
      </label>
      <label className="mini-label flex items-center gap-1.5">
        <input type="checkbox" checked={showSimilar} onChange={(e) => setShowSimilar(e.target.checked)} />
        相似边（虚线）
      </label>
      <label className="mini-label">相似边阈值：{sim.toFixed(2)}</label>
      <input
        type="range" min={40} max={80} value={Math.round(sim * 100)}
        className="w-full accent-[#4f9cf9]"
        onChange={(e) => setSim(+e.target.value / 100)}
      />
    </Section>
  );
}

function ClusterChips() {
  const graph = useApp((s) => s.graph);
  const isolateCluster = useApp((s) => s.isolateCluster);
  const toggleCluster = useApp((s) => s.toggleCluster);
  const counts = useMemo(() => {
    const c: Record<number, number> = {};
    graph.nodes.forEach((n) => (c[n.cluster] = (c[n.cluster] || 0) + 1));
    return Object.keys(c).map(Number).sort((a, b) => a - b).map((k) => [k, c[k]] as const);
  }, [graph]);

  return (
    <Section title="聚类">
      {counts.length === 0 && <span className="text-xs text-dim">暂无</span>}
      {counts.map(([c, n]) => (
        <span
          key={c}
          className={`chip ${isolateCluster === c ? "chip-on" : ""}`}
          onClick={() => toggleCluster(c)}
        >
          <span className="inline-block h-[9px] w-[9px] rounded-full" style={{ background: PALETTE[c % PALETTE.length] }} />
          簇 {c + 1} · {n}
        </span>
      ))}
    </Section>
  );
}

export default function LeftPanel() {
  const viewMode = useApp((s) => s.viewMode);
  return (
    <div className="overflow-y-auto border-r border-line bg-panel p-3">
      <SearchPanel />
      <FilterPanel />
      <AppearancePanel />
      {viewMode !== "kg" && <ClusterChips />}
    </div>
  );
}
