import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CornerDownLeft, Play, Search } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { useStore } from "../mock/store";
import { PaletteShell } from "../ui/Overlays";
import { cx } from "../ui/cx";

interface Item {
  id: string;
  group: string;
  label: string;
  hint?: string;
  run: () => void;
  icon?: typeof Play;
}

/** Deterministic command list with fuzzy search. No language model is involved (doc 20.2). */
export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { state } = useStore();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);

  const items = useMemo<Item[]>(() => {
    const go = (to: string) => () => navigate(to);
    const nav: Item[] = (
      [
        ["/dashboard", "nav.command"],
        ["/prospects", "nav.prospects"],
        ["/conversations", "nav.conversations"],
        ["/history", "nav.history"],
        ["/agent", "nav.agent"],
        ["/settings", "nav.settings"],
      ] as Array<[string, MessageKey]>
    ).map(([to, key]) => ({ id: to, group: t("palette.group.goTo"), label: t(key), run: go(to) }));
    const people: Item[] = state.prospects.map((p) => ({ id: `p-${p.id}`, group: t("nav.prospects"), label: p.name, hint: p.place, run: go(`/prospects?open=${p.id}`) }));
    return [...nav, ...people];
  }, [state.prospects, navigate, t]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items.slice(0, 9);
    return items.filter((i) => `${i.label} ${i.hint ?? ""}`.toLowerCase().includes(q)).slice(0, 12);
  }, [items, query]);

  const run = (item: Item | undefined) => {
    if (!item) return;
    onOpenChange(false);
    item.run();
  };

  return (
    <PaletteShell
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) {
          setQuery("");
          setActive(0);
        }
      }}
      label={t("bar.search")}
    >
      <div className="palette__field">
        <Search size={20} aria-hidden className="c-3" />
        <input
          autoFocus
          className="palette__input"
          placeholder={t("bar.search")}
          value={query}
          role="combobox"
          aria-expanded="true"
          aria-controls="palette-list"
          aria-activedescendant={results[active] ? `pal-${results[active]!.id}` : undefined}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((a) => Math.min(a + 1, results.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => Math.max(a - 1, 0));
            } else if (e.key === "Enter") {
              e.preventDefault();
              run(results[active]);
            }
          }}
        />
        <kbd className="kbd">Esc</kbd>
      </div>
      <ul id="palette-list" role="listbox" className="palette__list">
        {results.length === 0 ? <li className="palette__empty c-3">{t("palette.noMatches")}</li> : null}
        {results.map((r, i) => {
          const showGroup = i === 0 || results[i - 1]!.group !== r.group;
          const Icon = r.icon;
          return (
            <li key={r.id} role="presentation">
              {showGroup ? <p className="palette__group t-overline c-3">{r.group}</p> : null}
              <button id={`pal-${r.id}`} type="button" role="option" aria-selected={i === active} className={cx("palette__item", i === active && "is-active")} onMouseEnter={() => setActive(i)} onClick={() => run(r)}>
                {Icon ? <Icon size={16} aria-hidden /> : null}
                <span>{r.label}</span>
                {r.hint ? <span className="c-3 t-body-sm">{r.hint}</span> : null}
                <span className="spacer" />
                {i === active ? <CornerDownLeft size={14} aria-hidden className="c-3" /> : null}
              </button>
            </li>
          );
        })}
      </ul>
    </PaletteShell>
  );
}
