import { useId, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from "react";
import { motion } from "motion/react";
import { Lock, Quote } from "lucide-react";
import { cx } from "./cx";
import { useI18n } from "../i18n";

/* ── Segmented control: sliding glass thumb (doc 12.2) ── */
interface SegmentedProps<T extends string> {
  value: T;
  onChange: (value: T) => void;
  options: Array<{ value: T; label: string; icon?: ReactNode }>;
  label: string;
  size?: "sm" | "md";
  layoutId: string;
}

export function Segmented<T extends string>({ value, onChange, options, label, size = "md", layoutId }: SegmentedProps<T>) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label} data-size={size}>
      {options.map((o) => {
        const selected = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={selected ? 0 : -1}
            className="segmented__item"
            data-selected={selected || undefined}
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => {
              const i = options.findIndex((x) => x.value === value);
              const rtl = document.documentElement.dir === "rtl";
              const forward = rtl ? "ArrowLeft" : "ArrowRight";
              const back = rtl ? "ArrowRight" : "ArrowLeft";
              if (e.key === forward || e.key === "ArrowDown") {
                e.preventDefault();
                const next = options[(i + 1) % options.length];
                if (next) onChange(next.value);
              } else if (e.key === back || e.key === "ArrowUp") {
                e.preventDefault();
                const prev = options[(i - 1 + options.length) % options.length];
                if (prev) onChange(prev.value);
              }
            }}
          >
            {selected ? (
              <motion.span layoutId={layoutId} className="segmented__thumb" transition={{ type: "spring", stiffness: 380, damping: 32 }} />
            ) : null}
            <span className="segmented__content">
              {o.icon}
              {o.label}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/* ── Switch ── */
export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  const { t } = useI18n();
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className="switch"
      data-checked={checked || undefined}
      onClick={() => onChange(!checked)}
    >
      <span className="switch__thumb" />
      <span className="sr-only">{checked ? t("common.on") : t("common.off")}</span>
    </button>
  );
}

/* ── Field ── */
interface FieldShellProps {
  label: string;
  helper?: string;
  error?: string;
  optional?: string;
  id: string;
  badge?: ReactNode;
  children: ReactNode;
}

export function FieldShell({ label, helper, error, id, badge, optional, children }: FieldShellProps) {
  return (
    <div className="field" data-invalid={error ? true : undefined}>
      <div className="field__row">
        <label htmlFor={id} className="t-label c-2">
          {label}
          {optional ? <span className="c-3"> · {optional}</span> : null}
        </label>
        {badge}
      </div>
      {children}
      {error ? (
        <p id={`${id}-msg`} className="field__msg c-danger t-body-sm">
          {error}
        </p>
      ) : helper ? (
        <p id={`${id}-msg`} className="field__msg c-3 t-body-sm">
          {helper}
        </p>
      ) : null}
    </div>
  );
}

interface TextInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  label: string;
  helper?: string;
  error?: string;
  optional?: string;
}

export function TextInput({ label, helper, error, optional, className, ...rest }: TextInputProps) {
  const id = useId();
  return (
    <FieldShell id={id} label={label} helper={helper} error={error} optional={optional}>
      <input
        id={id}
        className={cx("input well", className)}
        aria-invalid={error ? true : undefined}
        aria-describedby={error || helper ? `${id}-msg` : undefined}
        {...rest}
      />
    </FieldShell>
  );
}

interface TextareaProps extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id"> {
  label: string;
  helper?: string;
  error?: string;
}

export function Textarea({ label, helper, error, className, ...rest }: TextareaProps) {
  const id = useId();
  return (
    <FieldShell id={id} label={label} helper={helper} error={error}>
      <textarea
        id={id}
        className={cx("input well", className)}
        rows={3}
        aria-invalid={error ? true : undefined}
        aria-describedby={error || helper ? `${id}-msg` : undefined}
        {...rest}
      />
    </FieldShell>
  );
}

/* ── Locked field: a value set elsewhere (platform administrator), shown as a real static
 *  display rather than a fake-editable input — no cursor, no focus ring, no typing affordance. */
export function LockedField({ label, value, helper }: { label: string; value: string; helper?: string }) {
  const id = useId();
  const { t } = useI18n();
  return (
    <FieldShell
      id={id}
      label={label}
      helper={helper}
      badge={
        <span className="locked-badge">
          <Lock size={12} aria-hidden />
          {t("controls.locked")}
        </span>
      }
    >
      <div id={id} className="input well locked-field" data-locked aria-readonly="true">
        {value}
      </div>
    </FieldShell>
  );
}

/* ── Verbatim input: exact values kept character for character (doc 12.5) ── */
export function VerbatimBadge() {
  const { t } = useI18n();
  return (
    <span className="verbatim-badge">
      <Lock size={12} aria-hidden />
      {t("verbatim.badge")}
    </span>
  );
}

interface VerbatimFieldProps {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  multiline?: boolean;
  previewPrefix?: string;
  previewSuffix?: string;
}

export function VerbatimField({ label, value, onChange, placeholder, multiline, previewPrefix = "", previewSuffix = "" }: VerbatimFieldProps) {
  const id = useId();
  const { t } = useI18n();
  return (
    <FieldShell id={id} label={label} badge={<VerbatimBadge />}>
      {multiline ? (
        <textarea
          id={id}
          className="input input--mono well"
          rows={3}
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          aria-describedby={`${id}-cap`}
          spellCheck={false}
        />
      ) : (
        <input
          id={id}
          className="input input--mono well"
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          aria-describedby={`${id}-cap`}
          spellCheck={false}
        />
      )}
      <p id={`${id}-cap`} className="c-3 t-body-sm field__msg">
        {t("verbatim.caption")}
      </p>
      <div className="verbatim-preview" aria-live="polite">
        <Quote size={14} aria-hidden />
        <span>
          {previewPrefix}
          <span className="verbatim-span">{value || "…"}</span>
          {previewSuffix}
        </span>
      </div>
    </FieldShell>
  );
}

/* ── Multi-select chips (languages, days) ── */
export function ToggleChips({
  options,
  value,
  onChange,
  label,
}: {
  options: Array<{ value: string; label: string }>;
  value: string[];
  onChange: (v: string[]) => void;
  label: string;
}) {
  return (
    <div className="toggle-chips" role="group" aria-label={label}>
      {options.map((o) => {
        const on = value.includes(o.value);
        return (
          <button
            key={o.value}
            type="button"
            className="toggle-chip"
            aria-pressed={on}
            onClick={() => onChange(on ? value.filter((v) => v !== o.value) : [...value, o.value])}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
