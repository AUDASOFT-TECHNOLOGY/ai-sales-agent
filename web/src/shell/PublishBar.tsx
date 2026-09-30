import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { AlertTriangle, CheckCircle2, Rocket } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { useStore } from "../mock/store";
import { Button } from "../ui/Button";
import { Glass } from "../ui/Glass";
import { Modal } from "../ui/Overlays";

/** The unpublished-changes bar and the Review sheet (docs C3 pattern 4, C4.10). Edits are a draft until published. */
export function PublishBar() {
  const { t } = useI18n();
  const { state, dispatch, toast } = useStore();
  const [open, setOpen] = useState(false);
  const count = state.changes.length;

  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty("--publish-offset", count > 0 ? "72px" : "0px");
    return () => {
      root.style.removeProperty("--publish-offset");
    };
  }, [count]);

  const version = state.publishedVersion + 1;
  return (
    <>
      <AnimatePresence>
        {count > 0 ? (
          <motion.div
            className="publish-bar-wrap"
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 16 }}
            transition={{ type: "spring", stiffness: 300, damping: 28 }}
          >
            <Glass level={2} tint="action" className="publish-bar" role="region" aria-label={t("configure.reviewTitle")}>
              <span className="publish-bar__dot" aria-hidden />
              <span className="t-body-sm">{count === 1 ? t("configure.unpublished.one") : t("configure.unpublished", { n: count })}</span>
              <Button size="sm" variant="primary" onClick={() => setOpen(true)}>
                {t("action.review")}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => dispatch({ type: "discard" })}>
                {t("action.discard")}
              </Button>
            </Glass>
          </motion.div>
        ) : null}
      </AnimatePresence>
      <Modal
        open={open && count > 0}
        onOpenChange={setOpen}
        size="lg"
        title={t("configure.reviewTitle")}
        description={t("publishBar.draftBasedOn", { n: state.publishedVersion })}
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              {t("action.cancel")}
            </Button>
            <Button
              variant="primary"
              leading={<Rocket size={16} aria-hidden />}
              onClick={() => {
                dispatch({ type: "publish" });
                setOpen(false);
                toast({
                  tone: "success",
                  title: t("configure.published", { n: version }),
                  body: t("publishBar.appliesNextCycle"),
                  undo: () => toast({ tone: "info", title: t("publishBar.rolledBackTo", { n: state.publishedVersion }) }),
                });
              }}
            >
              {t("action.publish")} v{version}
            </Button>
          </>
        }
      >
        <ul className="review-list">
          {state.changes.map((c) => {
            const [key, ...rest] = c.split("::");
            const value = rest.join("::");
            return (
              <li key={c} className="row">
                <span className="review-list__mark" aria-hidden />
                <span>{value ? t(key as MessageKey, { value }) : t(key as MessageKey)}</span>
              </li>
            );
          })}
        </ul>
        <div className="stack">
          <p className="t-label c-2">{t("configure.checks")}</p>
          <p className="row c-success t-body-sm">
            <CheckCircle2 size={16} aria-hidden /> {t("publishBar.checks.structure")}
          </p>
          <p className="row c-success t-body-sm">
            <CheckCircle2 size={16} aria-hidden /> {t("publishBar.checks.references")}
          </p>
          <p className="row c-attention t-body-sm">
            <AlertTriangle size={16} aria-hidden /> {t("publishBar.checks.suggestion")}
          </p>
        </div>
      </Modal>
    </>
  );
}
