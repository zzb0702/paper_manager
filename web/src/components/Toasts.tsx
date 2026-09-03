import { useApp } from "../store";

export default function Toasts() {
  const toasts = useApp((s) => s.toasts);
  const loading = useApp((s) => s.loading);

  return (
    <>
      {loading > 0 && (
        <div className="fixed top-2.5 right-3.5 z-[60] text-xs text-dim">加载中…</div>
      )}
      <div className="fixed right-3.5 bottom-3.5 z-[60]">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`mt-2 max-w-[420px] rounded-lg border border-line bg-raise px-3.5 py-2 ${
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
