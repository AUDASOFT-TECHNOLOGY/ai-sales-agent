/** Small building blocks shared by the Client screens, so every page reads and looks the same:
 *  page header, sub-navigation tabs, notes (info / success / warning / error), empty states, cards. */

import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { Button } from "../ui/Button";
import { Glass } from "../ui/Glass";
import { cx } from "../ui/cx";
import { ClientApiError } from "./api";

export function PageHead({ title, text, actions }: { title: string; text?: string; actions?: ReactNode }) {
  return (
    <header className="rv-head">
      <div className="rv-head__text">
        <h2>{title}</h2>
        {text ? <p>{text}</p> : null}
      </div>
      {actions ? <div className="rv-actions">{actions}</div> : null}
    </header>
  );
}

export function SubTabs({ label, items }: { label: string; items: Array<{ to: string; label: string; badge?: number }> }) {
  return (
    <nav className="rv-tabs" aria-label={label}>
      {items.map((item) => (
        <NavLink key={item.to} to={item.to} end className={({ isActive }) => cx("rv-tabs__item", isActive && "is-active")}>
          {item.label}
          {item.badge ? <span className="rv-badge">{item.badge}</span> : null}
        </NavLink>
      ))}
    </nav>
  );
}

type Tone = "info" | "success" | "warn" | "danger";
const TONE_ICON = { info: Info, success: CheckCircle2, warn: AlertTriangle, danger: XCircle } as const;

export function Note({ tone = "info", title, children, role }: { tone?: Tone; title?: string; children?: ReactNode; role?: "alert" | "status" }) {
  const Icon = TONE_ICON[tone];
  return (
    <div className="rv-note" data-tone={tone} role={role ?? (tone === "danger" ? "alert" : "status")}>
      <Icon size={18} aria-hidden />
      <div className="rv-note__body">
        {title ? <strong>{title}</strong> : null}
        {children ? <div>{children}</div> : null}
      </div>
    </div>
  );
}

export function Card({ children, flush, className, ...rest }: { children: ReactNode; flush?: boolean; className?: string; [key: string]: unknown }) {
  return (
    <Glass level={1} className={cx("rv-card", flush && "rv-card--flush", className)} {...rest}>
      {children}
    </Glass>
  );
}

export function Empty({ icon, title, text, action }: { icon: ReactNode; title: string; text?: string; action?: ReactNode }) {
  return (
    <div className="rv-empty">
      <span className="rv-empty__icon" aria-hidden>
        {icon}
      </span>
      <h3>{title}</h3>
      {text ? <p>{text}</p> : null}
      {action}
    </div>
  );
}

export function Loading() {
  const { t } = useI18n();
  return (
    <p className="c-3" role="status">
      {t("rv.common.loading")}
    </p>
  );
}

/** What the client is told when a call failed. `detail` holds the server's own rule sentences, which
 *  say what is wrong with the input and never repeat a value (they are shown only for 409 and 422). */
export interface Failure {
  key: MessageKey;
  detail?: string[];
  signedOut?: boolean;
  /** The server's state moved: what is on screen must be read again. */
  reload?: boolean;
}

export function failureOf(err: unknown): Failure {
  if (err instanceof ClientApiError) {
    switch (err.status) {
      case 0:
        return { key: "rv.err.network" };
      case 401:
        return { key: "rv.err.session", signedOut: true };
      case 403:
        return { key: "rv.err.forbidden" };
      case 404:
        return { key: "rv.err.notFound", reload: true };
      case 409:
        return { key: "rv.err.conflict", reload: true };
      case 413:
        return { key: "rv.err.tooBig" };
      case 422:
        return { key: "rv.err.invalid", detail: err.message.split("; ").filter((line) => line !== "") };
      case 429:
      case 502:
      case 503:
        return { key: "rv.err.unavailable" };
    }
  }
  return { key: "rv.err.generic" };
}

export function FailureNote({ failure, onRetry }: { failure: Failure; onRetry?: () => void }) {
  const { t } = useI18n();
  return (
    <Note tone="danger" title={t(failure.key)}>
      {failure.detail && failure.detail.length > 0 ? (
        <ul className="stack" dir="auto">
          {failure.detail.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      ) : null}
      {onRetry ? (
        <div style={{ marginBlockStart: 8 }}>
          <Button size="sm" onClick={onRetry}>
            {t("rv.common.retry")}
          </Button>
        </div>
      ) : null}
    </Note>
  );
}

/** A list of short texts the client adds to and removes from (benefits, target audience...). */
export function ListEditor({
  label,
  helper,
  values,
  onChange,
  placeholder,
  addLabel,
  maxItems = 20,
  disabled,
}: {
  label: string;
  helper?: string;
  values: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  addLabel: string;
  maxItems?: number;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  return (
    <fieldset className="rv-fieldset" disabled={disabled}>
      <legend className="t-label c-2">{label}</legend>
      {helper ? <p className="c-3 t-body-sm">{helper}</p> : null}
      {values.map((value, i) => (
        <div className="row" key={i}>
          <input
            className="input well"
            aria-label={`${label} ${i + 1}`}
            value={value}
            placeholder={placeholder}
            maxLength={300}
            onChange={(e) => onChange(values.map((v, n) => (n === i ? e.target.value : v)))}
            dir="auto"
          />
          <Button type="button" variant="ghost" size="sm" onClick={() => onChange(values.filter((_, n) => n !== i))} aria-label={`${t("rv.common.remove")} ${i + 1}`}>
            {t("rv.common.remove")}
          </Button>
        </div>
      ))}
      {values.length < maxItems ? (
        <div>
          <Button type="button" size="sm" onClick={() => onChange([...values, ""])}>
            {addLabel}
          </Button>
        </div>
      ) : null}
    </fieldset>
  );
}
