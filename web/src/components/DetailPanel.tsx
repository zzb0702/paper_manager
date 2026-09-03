import { useEffect, useState } from "react";
import { api } from "../api";
import { useApp } from "../store";
import type { EntityDetail, PaperDetail, RelatedItem } from "../types";

function RelatedSection({
  label,
  items,
  showSim,
}: {
  label: string;
  items: RelatedItem[];
  showSim?: boolean;
}) {
  const selectPaper = useApp((s) => s.selectPaper);
  if (!items?.length) return null;
  return (
    <>
      <h4 className="mt-3 mb-1 text-xs text-accent">{label}</h4>
      {items.map((c) => (
        <div key={c.paper_id} className="rel" onClick={() => selectPaper(c.paper_id)}>
          [{c.paper_id}] {c.title}
          {c.year ? ` (${c.year})` : ""}
          {showSim && c.similarity != null && <small className="text-dim"> 相似度 {c.similarity}</small>}
        </div>
      ))}
    </>
  );
}

function PaperPanel({ pid }: { pid: number }) {
  const closePanel = useApp((s) => s.closePanel);
  const openMarkdown = useApp((s) => s.openMarkdown);
  const load = useApp((s) => s.load);
  const toast = useApp((s) => s.toast);
  const [detail, setDetail] = useState<PaperDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [abstractOpen, setAbstractOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setDetail(null);
    setError(null);
    setAbstractOpen(false);
    let dead = false;
    api.paper(pid).then((p) => !dead && setDetail(p)).catch((e) => !dead && setError(String(e)));
    return () => { dead = true; };
  }, [pid]);

  async function fetchCitations() {
    setBusy(true);
    toast("正在抓取引文（OpenAlex）…");
    try {
      const r = await api.fetchCitations(pid);
      if (r.status === "not_found") {
        toast("数据源暂未收录该论文，之后可重试", "err");
      } else {
        toast(
          `已抓取：参考文献 ${r.refs}，被引 ${r.cited}` +
            (r.cited_by_count ? `（累计被引 ${r.cited_by_count}）` : ""),
          "ok",
        );
        await load();
        reload();
      }
    } catch (e) {
      toast(`抓取失败: ${e instanceof Error ? e.message : e}`, "err");
    } finally {
      setBusy(false);
    }
  }

  function reload() {
    setError(null);
    api.paper(pid).then(setDetail).catch((e) => setError(String(e)));
  }

  if (error) return <p className="text-err">{error}</p>;
  if (!detail) return <i className="text-dim">加载中…</i>;

  return (
    <>
      <button className="badge float-right cursor-pointer" onClick={closePanel}>✕ 关闭</button>
      <h2 className="mt-0.5 mb-1.5 text-[15px] font-semibold">{detail.title}</h2>
      <div className="mb-2 text-xs text-dim">
        {detail.authors || "佚名"} · {detail.year || "?"}
        {detail.doi && (
          <> · <a className="text-accent" href={`https://doi.org/${detail.doi}`} target="_blank" rel="noreferrer">DOI</a></>
        )}
        {" "}
        <span className="badge">{detail.engine}</span>
        {detail.cited_by_count ? <span className="badge">被引 {detail.cited_by_count}</span> : null}
        {detail.ext_counts?.refs ? <span className="badge">参考文献 {detail.ext_counts.refs}</span> : null}
      </div>
      <div className="my-2.5 flex flex-wrap gap-1.5">
        <button className="btn" disabled={busy} onClick={fetchCitations}>🔗 抓取引文</button>
        <button className="btn" onClick={() => openMarkdown(pid)}>📄 Markdown</button>
        <button className="btn" onClick={() => window.open(api.pdfUrl(pid))}>📕 原始 PDF</button>
      </div>
      {detail.summary && <div className="summary-box">{detail.summary}</div>}
      {detail.abstract && (
        <>
          <div
            className={`overflow-y-auto whitespace-pre-wrap text-xs text-dim ${abstractOpen ? "" : "max-h-[150px]"}`}
          >
            {detail.abstract}
          </div>
          <span className="cursor-pointer text-xs text-accent" onClick={() => setAbstractOpen(!abstractOpen)}>
            展开/收起
          </span>
        </>
      )}
      <RelatedSection label="引用（库内）" items={detail.related?.cites} />
      <RelatedSection label="被引（库内）" items={detail.related?.cited_by} />
      <RelatedSection label="语义相近" items={detail.related?.semantic} showSim />
    </>
  );
}

function EntityPanel({ eid }: { eid: number }) {
  const closePanel = useApp((s) => s.closePanel);
  const showEntity = useApp((s) => s.showEntity);
  const selectPaper = useApp((s) => s.selectPaper);
  const [detail, setDetail] = useState<EntityDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetail(null);
    setError(null);
    let dead = false;
    api.entity(eid).then((e) => !dead && setDetail(e)).catch((e) => !dead && setError(String(e)));
    return () => { dead = true; };
  }, [eid]);

  if (error) return <p className="text-err">{error}</p>;
  if (!detail) return <i className="text-dim">加载中…</i>;

  return (
    <>
      <button className="badge float-right cursor-pointer" onClick={closePanel}>✕ 关闭</button>
      <h2 className="mt-0.5 mb-1.5 text-[15px] font-semibold">{detail.name}</h2>
      <div className="mb-2 text-xs text-dim">
        <span className="badge">{detail.type}</span>
      </div>
      {detail.desc && <div className="summary-box">{detail.desc}</div>}
      {detail.neighbors.length > 0 && (
        <>
          <h4 className="mt-3 mb-1 text-xs text-accent">关系（{detail.neighbors.length}）</h4>
          {detail.neighbors.map((n, i) => (
            <div key={n.id + ":" + i} className="rel" onClick={() => showEntity(n.id)}>
              {n.direction === "→" ? "→" : "←"} <b>{n.relation}</b>{" "}
              {n.direction === "→" ? "" : "→"} {n.name} <small className="text-dim">（{n.type}）</small>
            </div>
          ))}
        </>
      )}
      {detail.papers.length > 0 && (
        <>
          <h4 className="mt-3 mb-1 text-xs text-accent">相关论文（{detail.papers.length}）</h4>
          {detail.papers.map((p) => (
            <div key={p.paper_id} className="rel" onClick={() => selectPaper(p.paper_id)}>
              [{p.paper_id}] {p.title}{p.year ? ` (${p.year})` : ""}
            </div>
          ))}
        </>
      )}
    </>
  );
}

export default function DetailPanel() {
  const selectedPaper = useApp((s) => s.selectedPaper);
  const selectedEntity = useApp((s) => s.selectedEntity);

  return (
    <aside className="overflow-y-auto border-l border-line bg-panel p-3">
      {selectedPaper != null ? (
        <PaperPanel pid={selectedPaper} />
      ) : selectedEntity != null ? (
        <EntityPanel eid={selectedEntity} />
      ) : null}
    </aside>
  );
}
