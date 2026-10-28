/** A list of phrases typed one at a time (a stop word or a whole sentence), shown as removable chips,
 *  with ready-made suggestions to add in one click. No separator to remember, so a phrase may hold commas. */

import { useState } from "react";
import { Plus, X } from "lucide-react";
import { useI18n } from "../../i18n";
import { Button } from "../../ui/Button";

const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

export function PhraseList({
  label,
  helper,
  placeholder,
  values,
  suggestions,
  onChange,
  addLabel,
  max = 30,
}: {
  label: string;
  helper?: string;
  placeholder?: string;
  values: string[];
  suggestions: string[];
  onChange: (next: string[]) => void;
  addLabel: string;
  max?: number;
}) {
  const { t } = useI18n();
  const [text, setText] = useState("");
  const has = (phrase: string) => values.some((v) => same(v, phrase));
  const add = (phrase: string) => {
    const clean = phrase.trim().replace(/\s+/g, " ");
    if (clean === "" || has(clean) || values.length >= max) return;
    onChange([...values, clean]);
    setText("");
  };
  const free = suggestions.filter((s) => !has(s));

  return (
    <div className="rv-phrases">
      <label className="rv-select-label">
        <span className="t-label c-2">{label}</span>
        <span className="rv-phrases__add">
          <input
            className="input well"
            value={text}
            placeholder={placeholder}
            maxLength={200}
            dir="auto"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              // Enter adds the phrase; it must not send the whole questionnaire form.
              if (e.key === "Enter") {
                e.preventDefault();
                add(text);
              }
            }}
          />
          <Button type="button" variant="secondary" leading={<Plus size={16} aria-hidden />} onClick={() => add(text)} disabled={text.trim() === ""}>
            {addLabel}
          </Button>
        </span>
        {helper ? <span className="c-3 t-body-sm">{helper}</span> : null}
      </label>

      {values.length > 0 ? (
        <ul className="rv-phrases__list" aria-label={label}>
          {values.map((v) => (
            <li key={v} className="rv-phrases__chip">
              <span dir="auto">{v}</span>
              <button type="button" onClick={() => onChange(values.filter((x) => x !== v))} aria-label={`${t("rv.common.remove")} : ${v}`}>
                <X size={14} aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {free.length > 0 ? (
        <div className="rv-phrases__suggest">
          <span className="c-3 t-body-sm">{t("rv.prep.a.stop.suggest")}</span>
          {free.map((s) => (
            <button key={s} type="button" className="rv-phrases__suggestion" onClick={() => add(s)} dir="auto">
              <Plus size={12} aria-hidden /> {s}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
