import { useApp } from "../store";

export default function Toasts() {
  const toasts = useApp((s) => s.toasts);
  const loading = useApp((s) => s.loading);

  return (
    <>
      {loading > 0 && (
        <div className="fixed top-3 right-3.5 z-[60] flex items-center gap-2 rounded-md border border-line bg-panel/95 px-2.5 py-1.5 text-[11.5px] text-dim shadow-lg shadow-black/30 backdrop-blur-md">
          <span className="inline-block h-3 w-3 animate-spin rounded-full border border-line border-t-accent" />
          加载中…
        </div>
      )}
      <div className="fixed right-3.5 bottom-3.5 z-[60] flex flex-col items-end gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`max-w-[400px] rounded-lg border border-line bg-panel/95 px-3.5 py-2.5 text-[12.5px] shadow-lg shadow-black/40 backdrop-blur-md ${
              t.kind === "err"
                ? "border-l-[3px] border-l-err"
                : t.kind === "ok"
                  ? "border-l-[3px] border-l-ok"
                  : "border-l-[3px] border-l-accent"
            }`}
          >
            {t.msg}
          </div>
        ))}
      </div>
    </>
  );
}
