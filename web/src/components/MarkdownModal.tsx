import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api";
import { useApp } from "../store";

export default function MarkdownModal() {
  const markdownFor = useApp((s) => s.markdownFor);
  const closeMarkdown = useApp((s) => s.closeMarkdown);
  const byId = useApp((s) => s.byId);
  const toast = useApp((s) => s.toast);
  const [md, setMd] = useState<string | null>(null);

  useEffect(() => {
    if (markdownFor == null) return;
    setMd(null);
    let dead = false;
    api
      .markdown(markdownFor)
      .then((t) => {
        if (!dead) setMd(t);
      })
      .catch((e) => {
        if (!dead) {
          toast(`读取 Markdown 失败: ${e instanceof Error ? e.message : e}`, "err");
          closeMarkdown();
        }
      });
    return () => { dead = true; };
  }, [markdownFor, toast, closeMarkdown]);

  if (markdownFor == null) return null;
  const title = byId.get(markdownFor)?.title || `paper ${markdownFor}`;
  // Drop page markers (<!-- page:N -->) the local converter inserts.
  const body = (md || "").replace(/^[ \t]*<!--[\s\S]*?-->[ \t]*$/gm, "").slice(0, 200000);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(4,7,11,.72)] backdrop-blur-[2px]"
      onClick={(e) => e.target === e.currentTarget && closeMarkdown()}
    >
      <div className="flex h-[86vh] w-[min(860px,92vw)] flex-col overflow-hidden rounded-xl border border-line bg-panel shadow-2xl shadow-black/50">
        <div className="flex items-center justify-between gap-3 border-b border-line bg-raise/50 px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-2">
            <span className="badge shrink-0">Markdown</span>
            <b className="truncate text-[13px] font-medium text-ink">{title}</b>
          </div>
          <button className="btn btn-ghost" onClick={closeMarkdown} aria-label="关闭">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden>
              <path d="M2 2l8 8M10 2 2 10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <div className="md-body overflow-auto px-6 py-5">
          {md == null ? (
            <div className="flex items-center gap-2 text-dim">
              <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border border-line border-t-accent" />
              <span className="text-xs">加载中…</span>
            </div>
          ) : (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
          )}
        </div>
      </div>
    </div>
  );
}
