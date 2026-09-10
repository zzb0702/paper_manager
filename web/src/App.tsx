import { useEffect } from "react";
import ConceptGraph2D from "./components/ConceptGraph2D";
import DetailPanel from "./components/DetailPanel";
import Header from "./components/Header";
import LeftPanel from "./components/LeftPanel";
import MarkdownModal from "./components/MarkdownModal";
import PaperGraph2D from "./components/PaperGraph2D";
import Toasts from "./components/Toasts";
import { useApp } from "./store";
import type { ViewMode } from "./types";

function ViewToggle() {
  const viewMode = useApp((s) => s.viewMode);
  const setView = useApp((s) => s.setView);
  const btn = (mode: ViewMode, label: string) => (
    <button
      key={mode}
      data-on={viewMode === mode ? "1" : "0"}
      onClick={() => setView(mode)}
    >
      {label}
    </button>
  );
  return (
    <div className="view-seg absolute top-3 left-3 z-10">
      {btn("timeline", "时间轴")}
      {btn("force", "关系图")}
      {btn("kg", "概念图")}
    </div>
  );
}

export default function App() {
  const viewMode = useApp((s) => s.viewMode);
  const graph = useApp((s) => s.graph);

  // Boot: load graph, then honour ?view / ?paper / ?entity deep links.
  useEffect(() => {
    const s = useApp.getState();
    const params = new URLSearchParams(location.search);
    const view = params.get("view");
    const pid = params.get("paper");
    const eid = params.get("entity");
    void (async () => {
      await s.load();
      if (view === "kg") s.setView("kg");
      if (pid && useApp.getState().byId.has(+pid)) s.selectPaper(+pid);
      else if (eid && view === "kg") s.showEntity(+eid);
    })();
  }, []);

  // Esc closes the modal first, then the detail panel.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const st = useApp.getState();
      if (st.markdownFor != null) st.closeMarkdown();
      else st.closePanel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <Header />
      <main className="grid min-h-0 flex-1 grid-cols-[270px_1fr_370px]">
        <LeftPanel />
        <div className="relative min-w-0">
          <ViewToggle />
          {viewMode === "kg" ? <ConceptGraph2D /> : <PaperGraph2D />}
          {viewMode !== "kg" && graph.nodes.length === 0 && (
            <div className="pointer-events-none absolute inset-0 z-[4] flex flex-col items-center justify-center gap-3 text-dim">
              <div className="empty-orb" />
              <div className="text-[13px] text-ink/70">论文库还是空的</div>
              <div className="text-[12px]">
                点右上角「导入 PDF」开始建库
              </div>
            </div>
          )}
        </div>
        <DetailPanel />
      </main>
      <MarkdownModal />
      <Toasts />
    </div>
  );
}
