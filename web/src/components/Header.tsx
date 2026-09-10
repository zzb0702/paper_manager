import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../store";
import type { Status } from "../types";

function BrandMark() {
  return (
    <svg
      width="22"
      height="22"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden
      className="shrink-0"
    >
      <rect x="3" y="4" width="18" height="16" rx="3" stroke="#5b9dff" strokeWidth="1.5" opacity="0.9" />
      <path d="M8 4v16" stroke="#5b9dff" strokeWidth="1.5" opacity="0.45" />
      <circle cx="13.5" cy="10" r="1.6" fill="#f0b429" />
      <circle cx="17" cy="14.5" r="1.2" fill="#5b9dff" />
      <path d="M13.5 10 17 14.5" stroke="#7c8c9c" strokeWidth="1" opacity="0.7" />
      <path d="M10.5 9h-1.2M10.5 12h-1.2M10.5 15h-1.2" stroke="#7c8c9c" strokeWidth="1.2" strokeLinecap="round" opacity="0.55" />
    </svg>
  );
}

export default function Header() {
  const graph = useApp((s) => s.graph);
  const load = useApp((s) => s.load);
  const toast = useApp((s) => s.toast);
  const [status, setStatus] = useState<Status | null>(null);
  const [engine, setEngine] = useState("datalab");
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.status().then(setStatus).catch(() => {});
  }, [graph]);

  async function onFiles(files: FileList | null) {
    if (!files || !files.length) return;
    setImporting(true);
    for (const f of Array.from(files)) {
      toast(`导入 ${f.name}（${engine}）…`);
      try {
        const r = await api.ingest(f, engine);
        if (r.status === "duplicate") toast(`已存在: ${r.title}`, "ok");
        else if (r.status === "ok") {
          const cost = r.cost_usd != null ? `，$${r.cost_usd.toFixed(4)}` : "";
          toast(
            `导入成功 [${r.paper_id}] ${(r.title || "").slice(0, 40)}（${r.chunks} 块${cost}）`,
            "ok",
          );
        } else toast(JSON.stringify(r), "err");
      } catch (e) {
        toast(`导入失败: ${e instanceof Error ? e.message : e}`, "err");
      }
    }
    setImporting(false);
    if (fileRef.current) fileRef.current.value = "";
    await load();
  }

  return (
    <header className="panel-shell flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line px-3.5 py-2.5">
      <div className="flex items-center gap-2.5">
        <BrandMark />
        <div className="flex flex-col leading-tight">
          <span className="text-[13.5px] font-semibold tracking-tight text-ink">
            论文库
          </span>
          <span className="text-[10.5px] tracking-wide text-dim">
            Paper Manager
          </span>
        </div>
      </div>

      {status && (
        <div className="mr-auto ml-1 hidden items-center gap-2 text-[11.5px] text-dim sm:flex">
          <span className="inline-flex items-center gap-1 rounded border border-line bg-field/60 px-1.5 py-0.5 tabular-nums">
            <span className="text-accent">{status.papers}</span> 篇
          </span>
          <span className="inline-flex items-center gap-1 rounded border border-line bg-field/60 px-1.5 py-0.5 tabular-nums">
            <span className="text-accent">{status.chunks}</span> 块
          </span>
          <span className="inline-flex items-center gap-1 rounded border border-line bg-field/60 px-1.5 py-0.5 tabular-nums">
            <span className="text-accent">{status.vectors}</span> 向量
          </span>
        </div>
      )}
      {!status && <div className="mr-auto" />}

      <select
        className="input max-w-[200px]"
        value={engine}
        onChange={(e) => setEngine(e.target.value)}
        title="PDF 转换引擎"
      >
        <option value="datalab">datalab · 高保真</option>
        <option value="local">local · 本地免费</option>
      </select>
      <input
        ref={fileRef}
        type="file"
        accept=".pdf"
        multiple
        hidden
        onChange={(e) => onFiles(e.target.files)}
      />
      <button
        className="btn btn-primary"
        disabled={importing}
        onClick={() => fileRef.current?.click()}
        title="导入 PDF 到本地论文库"
      >
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden>
          <path
            d="M8 2v8M5 7l3 3 3-3M3 12.5h10"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        导入 PDF
      </button>
      <button
        className="btn btn-ghost"
        title="重新加载"
        onClick={() => void load()}
        aria-label="重新加载"
      >
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden>
          <path
            d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2.5v3h-3"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
    </header>
  );
}
