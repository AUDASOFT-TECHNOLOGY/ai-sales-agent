/** A short list of answers to pick one from: a card each, the picked one lit. An option may carry a
 *  one-line hint and an example of what it looks like. */

import { Check } from "lucide-react";

export interface Choice {
  value: string;
  label: string;
  hint?: string;
  /** A sample of the result, shown in italics under the hint. */
  example?: string;
}

export function Choices({ label, value, options, onChange, clearable }: { label: string; value: string; options: Choice[]; onChange: (value: string) => void; clearable?: boolean }) {
  return (
    <div className="rv-choices" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={value === o.value} className="rv-choice" onClick={() => onChange(clearable && value === o.value ? "" : o.value)}>
          <span className="rv-choice__mark" aria-hidden>
            {value === o.value ? <Check size={13} strokeWidth={3} /> : null}
          </span>
          <span className="rv-choice__text">
            <strong>{o.label}</strong>
            {o.hint ? <small>{o.hint}</small> : null}
            {o.example ? <em>{o.example}</em> : null}
          </span>
        </button>
      ))}
    </div>
  );
}
