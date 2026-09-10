import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../store";
import { PALETTE } from "../palette";
import type { Filters, SizeMode } from "../types";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-5">
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
          placeholder="语义 / 关键词检索…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && go()}
        />
        <button className="btn" onClick={go}>搜</button>
      </div>
      {busy && (
        <div className="card text-dim">
          <small>检索中…</small>
        </div>
      )}
      {!busy && hits && hits.length === 0 && (
        <div className="card text-dim">
          <small>没有找到相关论文</small>
        </div>
      )}
      {!busy &&
        hits?.map((h, i) => (
          <div
            key={h.chunk_id + ":" + i}
            className="card"
            onClick={() => selectPaper(h.paper_id)}
          >
            <b className="block text-xs font-medium text-ink">
              [{h.paper_id}] {h.title}
            </b>
            <small className="text-dim">
              {h.year || "?"} · {h.section}
              {h.pages ? ` · p${h.pages}` : ""} · {h.matched_chunks} 处相关
            </small>
          </div>
        ))}
    </Section>
  );
}

/** Popover year-grid picker — years come from the library, not a full calendar. */
function YearPicker({
  value,
  years,
  placeholder,
  onSelect,
  disabledYears,
}: {
  value: number | null;
  years: number[];
  placeholder: string;
  onSelect: (y: number | null) => void;
  disabledYears?: Set<number>;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative min-w-0 flex-1">
      <button
        type="button"
        className={`input flex w-full items-center justify-between gap-1 text-left ${
          value != null ? "text-ink" : "text-dim/80"
        }`}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="truncate tabular-nums">{value ?? placeholder}</span>
        <svg width="10" height="10" viewBox="0 0 12 12" fill="none" aria-hidden className="shrink-0">
          <path d="M2.5 4.5 6 8l3.5-3.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
      </button>
      {open && (
        <div
          role="listbox"
          className="absolute top-full left-0 z-30 mt-1 max-h-[220px] w-full min-w-[148px] overflow-y-auto rounded-lg border border-line bg-panel p-1.5 shadow-xl shadow-black/40"
        >
          <button
            type="button"
            className="mb-1 w-full rounded-md px-2 py-1.5 text-left text-[12px] text-dim hover:bg-raise hover:text-ink"
            onClick={() => {
              onSelect(null);
              setOpen(false);
            }}
          >
            不限
          </button>
          <div className="grid grid-cols-3 gap-0.5">
            {years.map((y) => {
              const disabled = disabledYears?.has(y) ?? false;
              const active = value === y;
              return (
                <button
                  key={y}
                  type="button"
                  disabled={disabled}
                  aria-selected={active}
                  className={`rounded-md px-1 py-1.5 text-center text-[12px] tabular-nums transition-colors ${
                    active
                      ? "bg-accent/20 font-medium text-accent"
                      : disabled
                        ? "cursor-not-allowed text-dim/30"
                        : "text-ink/80 hover:bg-raise hover:text-ink"
                  }`}
                  onClick={() => {
                    onSelect(y);
                    setOpen(false);
                  }}
                >
                  {y}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function FilterPanel() {
  const setFilters = useApp((s) => s.setFilters);
  const filters = useApp((s) => s.filters);
  const graph = useApp((s) => s.graph);
  const [author, setAuthor] = useState("");
  const [venue, setVenue] = useState("");

  const years = useMemo(() => {
    const set = new Set<number>();
    graph.nodes.forEach((n) => {
      if (n.year) set.add(n.year);
    });
    if (set.size === 0) return [];
    return Array.from(set).sort((a, b) => a - b);
  }, [graph]);

  const yearMin = filters?.yearMin ?? null;
  const yearMax = filters?.yearMax ?? null;

  // Years disabled in the min picker: >= chosen max would invert the range.
  const disabledMin = useMemo(() => {
    if (yearMax == null) return undefined;
    return new Set(years.filter((y) => y > yearMax));
  }, [years, yearMax]);
  const disabledMax = useMemo(() => {
    if (yearMin == null) return undefined;
    return new Set(years.filter((y) => y < yearMin));
  }, [years, yearMin]);

  function commitYear(nextMin: number | null, nextMax: number | null) {
    const f: Filters = { ...(filters || {}) };
    if (nextMin != null) f.yearMin = nextMin;
    else delete f.yearMin;
    if (nextMax != null) f.yearMax = nextMax;
    else delete f.yearMax;
    setFilters(Object.keys(f).length ? f : null);
  }

  function applyText() {
    const f: Filters = { ...(filters || {}) };
    if (author.trim()) f.author = author.trim().toLowerCase();
    else delete f.author;
    if (venue.trim()) f.venue = venue.trim().toLowerCase();
    else delete f.venue;
    setFilters(Object.keys(f).length ? f : null);
  }

  function clearAll() {
    setAuthor("");
    setVenue("");
    setFilters(null);
  }

  const hasFilter =
    yearMin != null || yearMax != null || !!author.trim() || !!venue.trim();

  return (
    <Section title="筛选">
      {years.length > 0 ? (
        <>
          <label className="mini-label">发表年份</label>
          <div className="mb-1.5 flex items-center gap-1.5">
            <YearPicker
              value={yearMin}
              years={years}
              placeholder="起始年"
              disabledYears={disabledMin}
              onSelect={(y) => commitYear(y, yearMax)}
            />
            <span className="shrink-0 text-dim">–</span>
            <YearPicker
              value={yearMax}
              years={years}
              placeholder="结束年"
              disabledYears={disabledMax}
              onSelect={(y) => commitYear(yearMin, y)}
            />
          </div>
        </>
      ) : (
        <div className="mb-1.5 text-[11px] text-dim">库中暂无论文年份数据</div>
      )}
      <div className="mb-1.5 flex gap-1.5">
        <input
          className="input flex-1"
          placeholder="作者包含…"
          value={author}
          onChange={(e) => setAuthor(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && applyText()}
        />
      </div>
      <div className="mb-1.5 flex gap-1.5">
        <input
          className="input flex-1"
          placeholder="期刊 / 会议包含…"
          value={venue}
          onChange={(e) => setVenue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && applyText()}
        />
      </div>
      <div className="flex gap-1.5">
        <button className="btn flex-1" onClick={applyText}>
          应用筛选
        </button>
        <button
          className={`btn ${hasFilter ? "btn-ghost" : "btn-ghost opacity-50"}`}
          onClick={clearAll}
          disabled={!hasFilter}
        >
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
      <div className="mb-1 flex gap-1.5">
        <select
          className="input w-full"
          value={sizeMode}
          onChange={(e) => setSizeMode(e.target.value as SizeMode)}
        >
          <option value="cited">被引数</option>
          <option value="chunks">章节块数</option>
          <option value="uniform">统一大小</option>
        </select>
      </div>
      <label className="mini-label flex cursor-pointer items-center gap-2">
        <input
          type="checkbox"
          checked={showCitation}
          onChange={(e) => setShowCitation(e.target.checked)}
        />
        引文边（实线箭头）
      </label>
      <label className="mini-label flex cursor-pointer items-center gap-2">
        <input
          type="checkbox"
          checked={showSimilar}
          onChange={(e) => setShowSimilar(e.target.checked)}
        />
        相似边（虚线）
      </label>
      <label className="mini-label tabular-nums">
        相似边阈值：{sim.toFixed(2)}
      </label>
      <input
        type="range"
        min={40}
        max={80}
        value={Math.round(sim * 100)}
        className="w-full"
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
    return Object.keys(c)
      .map(Number)
      .sort((a, b) => a - b)
      .map((k) => [k, c[k]] as const);
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
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: PALETTE[c % PALETTE.length] }}
          />
          簇 {c + 1}
          <span className="text-dim tabular-nums">· {n}</span>
        </span>
      ))}
    </Section>
  );
}

export default function LeftPanel() {
  const viewMode = useApp((s) => s.viewMode);
  return (
    <aside className="panel-shell overflow-y-auto border-r border-line px-3.5 py-3">
      <SearchPanel />
      <FilterPanel />
      <AppearancePanel />
      {viewMode !== "kg" && <ClusterChips />}
    </aside>
  );
}
