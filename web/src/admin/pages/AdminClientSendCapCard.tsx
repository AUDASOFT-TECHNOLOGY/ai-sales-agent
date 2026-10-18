/** A client's daily e-mail limit (GET/PUT/DELETE /admin/v1/clients/:id/send-cap).
 *
 *  The cap is the Admin's alone: there is no default and no "unlimited", and a client without a cap
 *  cannot send any e-mail (prospecting, answers, follow-ups and the daily report all count). What
 *  really limits the client is the effective cap, the smaller of the cap and the daily quota of its
 *  plan; the card shows both, with today's use. Only an owner sets or removes it. */

import { useEffect, useId, useState, type FormEvent } from "react";
import { Button } from "../../ui/Button";
import { TextInput } from "../../ui/Controls";
import { useAdminAuth } from "../AdminAuthContext";
import {
  AdminApiError,
  adminGetSendCap,
  adminRemoveSendCap,
  adminSetSendCap,
  type SendCap,
} from "../api";

// Mirrors require_owner on PUT/DELETE /clients/:id/send-cap.
const CAN_EDIT_ROLES = new Set(["owner"]);
const NOT_AVAILABLE = "—";

const STATUS_TEXT: Record<SendCap["status"], string> = {
  ok: "Emails can go out today.",
  not_configured: "No daily limit is set: this client cannot send any email.",
  limit_reached: "Today's limit is reached: nothing more goes out until tomorrow (UTC).",
};

export function AdminClientSendCapCard({ clientId }: { clientId: string }) {
  const { admin } = useAdminAuth();
  const canEdit = admin !== null && CAN_EDIT_ROLES.has(admin.role);
  const statusId = useId();
  const [cap, setCap] = useState<SendCap | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    adminGetSendCap(clientId)
      .then((c) => {
        if (cancelled) return;
        setCap(c);
        setValue(c.daily_cap === null ? "" : String(c.daily_cap));
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [clientId]);

  if (loadError)
    return <p className="admin-alert admin-alert--danger">Could not load the client&apos;s email limit.</p>;
  if (!cap) return <p className="c-2 t-body-sm">Loading…</p>;

  const parsed = /^\d+$/.test(value.trim()) ? Number(value.trim()) : null;
  const valid = parsed !== null && parsed >= 1 && parsed <= cap.max_cap;
  const dirty = parsed !== cap.daily_cap;

  async function run(action: () => Promise<SendCap>, failure: string) {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const next = await action();
      setCap(next);
      setValue(next.daily_cap === null ? "" : String(next.daily_cap));
      setSaved(true);
    } catch (err) {
      setError(err instanceof AdminApiError ? err.message : failure);
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (valid && parsed !== null) void run(() => adminSetSendCap(clientId, parsed), "Could not save the limit.");
  }

  const limited =
    cap.plan_daily_quota !== null && cap.daily_cap !== null && cap.plan_daily_quota < cap.daily_cap;

  return (
    <form className="admin-llm-config" onSubmit={onSubmit} aria-label="Daily email limit">
      <p id={statusId} data-testid="send-cap-status" role="status" className={cap.status === "ok" ? "c-success" : "c-3"}>
        {STATUS_TEXT[cap.status]}
      </p>
      <dl>
        <Row label="Limit set by the admin" value={cap.daily_cap} />
        <Row label="Daily quota of the plan" value={cap.plan_daily_quota} />
        <Row label="Effective limit" value={cap.effective_cap} />
        <Row label={`Sent today (${cap.day_utc}, UTC)`} value={cap.used_today} />
      </dl>
      {limited ? (
        <p className="c-3 t-body-sm">The plan&apos;s daily quota is lower than the limit: the quota applies.</p>
      ) : null}
      <TextInput
        label="Daily limit (emails per day)"
        inputMode="numeric"
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          setSaved(false);
        }}
        disabled={!canEdit}
        helper={`A whole number from 1 to ${cap.max_cap}.`}
        error={value !== "" && !valid ? `Enter a whole number from 1 to ${cap.max_cap}.` : undefined}
        autoComplete="off"
      />
      {error ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {error}
        </p>
      ) : null}
      {saved ? (
        <p className="admin-alert admin-alert--success" role="status">
          Saved.
        </p>
      ) : null}
      <div className="row">
        <Button
          type="submit"
          variant="primary"
          size="sm"
          loading={busy}
          disabled={!valid || !dirty}
          disabledReason={!canEdit ? "Only an owner can set a client's email limit." : undefined}
        >
          Save limit
        </Button>
        {cap.daily_cap !== null ? (
          <Button
            type="button"
            size="sm"
            disabled={busy}
            disabledReason={!canEdit ? "Only an owner can remove a client's email limit." : undefined}
            onClick={() => void run(() => adminRemoveSendCap(clientId), "Could not remove the limit.")}
          >
            Remove limit
          </Button>
        ) : null}
      </div>
    </form>
  );
}

function Row({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="admin-field-row">
      <dt className="c-3 t-body-sm">{label}</dt>
      <dd className="c-2">{value ?? NOT_AVAILABLE}</dd>
    </div>
  );
}
