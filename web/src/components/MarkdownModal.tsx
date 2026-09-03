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
      className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(5,8,12,.75)]"
      onClick={(e) => e.target === e.currentTarget && closeMarkdown()}
    >
      <div className="flex h-[86vh] w-[min(860px,92vw)] flex-col rounded-xl border border-line bg-panel">
        <div className="flex items-center justify-between border-b border-line px-3.5 py-2.5">
          <b className="truncate text-[13px]">{title}</b>
          <button className="btn" onClick={closeMarkdown}>✕</button>
        </div>
        <div className="md-body overflow-auto px-5 py-4">
          {md == null ? <i className="text-dim">加载中…</i> : <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>}
        </div>
      </div>
    </div>
  );
}
