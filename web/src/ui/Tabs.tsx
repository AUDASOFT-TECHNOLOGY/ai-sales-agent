import { useId, type ReactNode } from "react";
import { motion } from "motion/react";

interface TabsProps<T extends string> {
  value: T;
  onChange: (v: T) => void;
  tabs: Array<{ value: T; label: string }>;
  label: string;
  children: ReactNode;
}

/** Tabs with a sliding luminous underline. Arrow keys move between tabs (doc 20.4: in-screen tabs are the second level). */
export function Tabs<T extends string>({ value, onChange, tabs, label, children }: TabsProps<T>) {
  const base = useId();
  const focusTab = (v: T) => {
    onChange(v);
    requestAnimationFrame(() => document.getElementById(`${base}-tab-${v}`)?.focus());
  };
  return (
    <div className="tabs">
      <div className="tabs__list" role="tablist" aria-label={label}>
        {tabs.map((tab) => {
          const selected = tab.value === value;
          return (
            <button
              key={tab.value}
              id={`${base}-tab-${tab.value}`}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={`${base}-panel`}
              tabIndex={selected ? 0 : -1}
              className="tabs__tab"
              onClick={() => onChange(tab.value)}
              onKeyDown={(e) => {
                const i = tabs.findIndex((t) => t.value === value);
                const rtl = document.documentElement.dir === "rtl";
                const next = rtl ? "ArrowLeft" : "ArrowRight";
                const prev = rtl ? "ArrowRight" : "ArrowLeft";
                if (e.key === next) focusTab(tabs[(i + 1) % tabs.length]!.value);
                else if (e.key === prev) focusTab(tabs[(i - 1 + tabs.length) % tabs.length]!.value);
                else if (e.key === "Home") focusTab(tabs[0]!.value);
                else if (e.key === "End") focusTab(tabs[tabs.length - 1]!.value);
              }}
            >
              {tab.label}
              {selected ? <motion.span layoutId={`${base}-underline`} className="tabs__underline" transition={{ type: "spring", stiffness: 420, damping: 34 }} /> : null}
            </button>
          );
        })}
      </div>
      <div id={`${base}-panel`} role="tabpanel" aria-labelledby={`${base}-tab-${value}`} tabIndex={0} className="tabs__panel">
        {children}
      </div>
    </div>
  );
}
