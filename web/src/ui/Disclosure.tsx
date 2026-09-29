import { useId, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ChevronDown } from "lucide-react";

/** Reveals secondary content on demand ("show less, reveal more when needed"). The trigger is a real button with aria-expanded. */
export function Disclosure({ title, hint, defaultOpen = false, children }: { title: string; hint?: string; defaultOpen?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <div className="disclosure" data-open={open || undefined}>
      <button type="button" className="disclosure__btn" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
        <span className="disclosure__title">{title}</span>
        {hint ? <span className="disclosure__hint c-3">{hint}</span> : null}
        <ChevronDown size={20} aria-hidden className="disclosure__chev" />
      </button>
      <AnimatePresence initial={false}>
        {open ? (
          <motion.div
            id={id}
            key="panel"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
            className="disclosure__panel"
          >
            <div className="disclosure__inner">{children}</div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
