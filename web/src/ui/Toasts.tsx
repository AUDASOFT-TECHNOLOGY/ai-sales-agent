import { useEffect } from "react";
import { AnimatePresence, motion } from "motion/react";
import { AlertTriangle, CheckCircle2, Info, Sparkles, X, XCircle } from "lucide-react";
import { useStore, type Toast } from "../mock/store";
import { GlyphTile } from "./Chip";
import type { Tint } from "./Glass";
import { IconButton } from "./Button";
import { useI18n } from "../i18n";

const TONE: Record<Toast["tone"], { tint: Tint; icon: typeof Info; ms: number | null; role: "status" | "alert" }> = {
  success: { tint: "success", icon: CheckCircle2, ms: 4500, role: "status" },
  info: { tint: "action", icon: Info, ms: 4500, role: "status" },
  agent: { tint: "agent", icon: Sparkles, ms: 6000, role: "status" },
  attention: { tint: "attention", icon: AlertTriangle, ms: null, role: "status" },
  danger: { tint: "danger", icon: XCircle, ms: null, role: "alert" },
};

function ToastItem({ toast }: { toast: Toast }) {
  const { dispatch } = useStore();
  const { t } = useI18n();
  const cfg = TONE[toast.tone];
  const Icon = cfg.icon;

  useEffect(() => {
    if (cfg.ms === null) return;
    const id = window.setTimeout(() => dispatch({ type: "dismissToast", id: toast.id }), toast.undo ? 10000 : cfg.ms);
    return () => window.clearTimeout(id);
  }, [cfg.ms, dispatch, toast.id, toast.undo]);

  return (
    <motion.li
      layout
      initial={{ opacity: 0, y: 14, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 8, transition: { duration: 0.15 } }}
      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
      className="toast glass"
      data-level="2"
      data-tint={cfg.tint}
      role={cfg.role}
    >
      <GlyphTile tint={cfg.tint}>
        <Icon size={18} />
      </GlyphTile>
      <div className="toast__text">
        <p className="toast__title">{toast.title}</p>
        {toast.body ? <p className="c-2 t-body-sm">{toast.body}</p> : null}
      </div>
      {toast.undo ? (
        <button
          type="button"
          className="toast__action"
          onClick={() => {
            toast.undo?.();
            dispatch({ type: "dismissToast", id: toast.id });
          }}
        >
          {t("action.undo")}
        </button>
      ) : null}
      <IconButton label={t("common.close")} onClick={() => dispatch({ type: "dismissToast", id: toast.id })}>
        <X size={16} />
      </IconButton>
    </motion.li>
  );
}

export function ToastViewport() {
  const { state } = useStore();
  const { t } = useI18n();
  return (
    <ul className="toast-viewport" aria-label={t("toasts.notifications")}>
      <AnimatePresence initial={false}>
        {state.toasts.map((toast) => (
          <ToastItem key={toast.id} toast={toast} />
        ))}
      </AnimatePresence>
    </ul>
  );
}
