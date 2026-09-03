import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../store";
import type { Status } from "../types";

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
    <header className="flex flex-wrap items-center gap-2 border-b border-line bg-panel px-3 py-2">
      <h1 className="mr-1.5 text-[15px] whitespace-nowrap">📚 论文库</h1>
      <span className="mr-auto text-xs text-dim">
        {status ? `${status.papers} 篇 · ${status.chunks} 块 · ${status.vectors} 向量` : ""}
      </span>
      <select className="input" value={engine} onChange={(e) => setEngine(e.target.value)}>
        <option value="datalab">datalab（高保真，按页计费）</option>
        <option value="local">local（免费文本抽取）</option>
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
        className="btn"
        disabled={importing}
        onClick={() => fileRef.current?.click()}
      >
        📥 导入 PDF
      </button>
      <button className="btn" title="重新加载" onClick={() => void load()}>
        ↻
      </button>
    </header>
  );
}
