import { useEffect, useState } from "react";
import { api } from "../api";
import { useApp } from "../store";
import { formatAuthors, type EntityDetail, type PaperDetail, type RelatedItem } from "../types";

function CloseBtn() {
  const closePanel = useApp((s) => s.closePanel);
  return (
    <button className="btn btn-ghost absolute top-3 right-3" onClick={closePanel} aria-label="关闭面板">
      <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden>
        <path d="M2 2l8 8M10 2 2 10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    </button>
  );
}

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
    <div className="mt-4">
      <h4 className="mb-1.5 text-[10.5px] font-semibold tracking-[0.1em] text-dim uppercase">
        {label}
      </h4>
      <div className="space-y-0.5">
        {items.map((c) => (
          <div key={c.paper_id} className="rel" onClick={() => selectPaper(c.paper_id)}>
            <span className="text-accent/80 tabular-nums">[{c.paper_id}]</span>{" "}
            {c.title}
            {c.year ? <span className="text-dim"> ({c.year})</span> : null}
            {showSim && c.similarity != null && (
              <span className="ml-1 text-dim tabular-nums">· 相似度 {c.similarity}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function PaperPanel({ pid }: { pid: number }) {
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
    let dead = false;
    api
      .paper(pid)
      .then((p) => {
        if (!dead) setDetail(p);
      })
      .catch((e) => {
        if (!dead) setError(String(e));
      });
    return () => {
      dead = true;
    };
  }

  if (error) return <p className="mt-8 text-err">{error}</p>;
  if (!detail)
    return (
      <div className="mt-8 flex items-center gap-2 text-dim">
        <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border border-line border-t-accent" />
        <span className="text-xs">加载中…</span>
      </div>
    );

  return (
    <>
      <CloseBtn />
      <div className="pr-8">
        <div className="mb-1 flex items-center gap-1.5 text-[10.5px] tracking-wide text-dim">
          <span className="tabular-nums">#{detail.id}</span>
          <span className="text-dim/50">·</span>
          <span className="badge">{detail.engine}</span>
        </div>
        <h2 className="mt-0.5 mb-2 text-[15px] leading-snug font-semibold tracking-tight text-ink">
          {detail.title}
        </h2>
        <div className="mb-1 text-xs leading-relaxed text-dim">
          {formatAuthors(detail.authors) || "佚名"} · {detail.year || "?"}
          {detail.doi && (
            <>
              {" · "}
              <a
                className="text-accent underline-offset-2 hover:underline"
                href={`https://doi.org/${detail.doi}`}
                target="_blank"
                rel="noreferrer"
              >
                DOI
              </a>
            </>
          )}
        </div>
        {(detail.cited_by_count || detail.ext_counts?.refs) && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {detail.cited_by_count ? (
              <span className="badge tabular-nums">被引 {detail.cited_by_count}</span>
            ) : null}
            {detail.ext_counts?.refs ? (
              <span className="badge tabular-nums">参考文献 {detail.ext_counts.refs}</span>
            ) : null}
          </div>
        )}
      </div>
      <div className="my-3 flex flex-wrap gap-1.5">
        <button className="btn" disabled={busy} onClick={fetchCitations}>
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden>
            <path
              d="M6.5 9.5 9.5 6.5M7 4.5l1.2-1.2a2.8 2.8 0 0 1 4 4L11 8.5M9 11.5l-1.2 1.2a2.8 2.8 0 0 1-4-4L5 7.5"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
            />
          </svg>
          抓取引文
        </button>
        <button className="btn" onClick={() => openMarkdown(pid)}>
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden>
            <path
              d="M4 2.5h5.5L12 5v8.5H4v-11z"
              stroke="currentColor"
              strokeWidth="1.3"
              strokeLinejoin="round"
            />
            <path d="M9.5 2.5V5H12" stroke="currentColor" strokeWidth="1.3" />
          </svg>
          Markdown
        </button>
        <button className="btn" onClick={() => window.open(api.pdfUrl(pid))}>
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden>
            <path
              d="M4 2.5h5.5L12 5v8.5H4v-11z"
              stroke="currentColor"
              strokeWidth="1.3"
              strokeLinejoin="round"
            />
            <path d="M9.5 2.5V5H12" stroke="currentColor" strokeWidth="1.3" />
            <path d="M6 10.5h4" stroke="#f07178" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
          原始 PDF
        </button>
      </div>
      {detail.summary && <div className="summary-box">{detail.summary}</div>}
      {detail.abstract && (
        <>
          <div
            className={`overflow-y-auto text-xs leading-relaxed whitespace-pre-wrap text-dim ${
              abstractOpen ? "" : "max-h-[140px]"
            }`}
          >
            {detail.abstract}
          </div>
          <button
            className="mt-1 cursor-pointer text-xs text-accent hover:underline"
            onClick={() => setAbstractOpen(!abstractOpen)}
          >
            {abstractOpen ? "收起摘要" : "展开摘要"}
          </button>
        </>
      )}
      <RelatedSection label="引用（库内）" items={detail.related?.cites} />
      <RelatedSection label="被引（库内）" items={detail.related?.cited_by} />
      <RelatedSection label="语义相近" items={detail.related?.semantic} showSim />
    </>
  );
}

function EntityPanel({ eid }: { eid: number }) {
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

  if (error) return <p className="mt-8 text-err">{error}</p>;
  if (!detail)
    return (
      <div className="mt-8 flex items-center gap-2 text-dim">
        <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border border-line border-t-accent" />
        <span className="text-xs">加载中…</span>
      </div>
    );

  return (
    <>
      <CloseBtn />
      <div className="pr-8">
        <div className="mb-1 text-[10.5px] tracking-wide text-dim">知识实体</div>
        <h2 className="mt-0.5 mb-2 text-[15px] leading-snug font-semibold tracking-tight text-ink">
          {detail.name}
        </h2>
        <div className="mb-2">
          <span className="badge">{detail.type}</span>
        </div>
      </div>
      {detail.desc && <div className="summary-box">{detail.desc}</div>}
      {detail.neighbors.length > 0 && (
        <div className="mt-4">
          <h4 className="mb-1.5 text-[10.5px] font-semibold tracking-[0.1em] text-dim uppercase">
            关系（{detail.neighbors.length}）
          </h4>
          <div className="space-y-0.5">
            {detail.neighbors.map((n, i) => (
              <div key={n.id + ":" + i} className="rel" onClick={() => showEntity(n.id)}>
                {n.direction === "→" ? "→" : "←"} <b>{n.relation}</b>{" "}
                {n.direction === "→" ? "" : "→"} {n.name}{" "}
                <small className="text-dim">（{n.type}）</small>
              </div>
            ))}
          </div>
        </div>
      )}
      {detail.papers.length > 0 && (
        <div className="mt-4">
          <h4 className="mb-1.5 text-[10.5px] font-semibold tracking-[0.1em] text-dim uppercase">
            相关论文（{detail.papers.length}）
          </h4>
          <div className="space-y-0.5">
            {detail.papers.map((p) => (
              <div key={p.paper_id} className="rel" onClick={() => selectPaper(p.paper_id)}>
                <span className="text-accent/80 tabular-nums">[{p.paper_id}]</span>{" "}
                {p.title}
                {p.year ? <span className="text-dim"> ({p.year})</span> : null}
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}

export default function DetailPanel() {
  const selectedPaper = useApp((s) => s.selectedPaper);
  const selectedEntity = useApp((s) => s.selectedEntity);

  return (
    <aside className="panel-shell relative overflow-y-auto border-l border-line px-3.5 py-3">
      {selectedPaper != null ? (
        <PaperPanel pid={selectedPaper} />
      ) : selectedEntity != null ? (
        <EntityPanel eid={selectedEntity} />
      ) : (
        <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
          <div className="empty-orb opacity-70" />
          <div className="text-[13px] text-ink/60">未选择论文</div>
          <p className="max-w-[220px] text-[12px] leading-relaxed text-dim">
            在图上点击节点，查看摘要卡、引文与原文。
          </p>
        </div>
      )}
    </aside>
  );
}
