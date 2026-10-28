/** The few pieces every connection row shares: its busy/error state, the call that goes with them and
 *  the disconnect confirmation. Nothing here is specific to a connector. */

import { useState } from "react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { ClientApiError } from "../api";
import { Note } from "../kit";

/** What a failed call means to the client, in one of three sentences. */
export function errorKeyOf(err: unknown): MessageKey {
  if (err instanceof ClientApiError) {
    if (err.status === 503) return "rv.conn.err.unavailable";
    if (err.status === 409 || err.status === 403) return "rv.conn.err.conflict";
  }
  return "rv.conn.err.generic";
}

export function goTo(url: string): void {
  window.location.assign(url);
}

export function useRowState() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<MessageKey | null>(null);
  const [confirming, setConfirming] = useState(false);

  /** Runs one call with the row busy; a failure is said in a sentence, never in the provider's words. */
  async function run(call: () => Promise<unknown>, after?: () => Promise<void> | void) {
    setError(null);
    setBusy(true);
    try {
      await call();
      await after?.();
    } catch (err) {
      setError(errorKeyOf(err));
    }
    setBusy(false);
  }

  return { busy, error, confirming, setConfirming, setError, setBusy, run };
}

export function RowError({ error }: { error: MessageKey | null }) {
  const { t } = useI18n();
  return error ? <Note tone="danger">{t(error)}</Note> : null;
}

export function DisconnectConfirm({ busy, onYes, onNo }: { busy: boolean; onYes: () => void; onNo: () => void }) {
  const { t } = useI18n();
  return (
    <Note tone="warn">
      <span>{t("rv.conn.confirm.text")}</span>
      <span className="rv-actions" style={{ marginBlockStart: 8 }}>
        <Button size="sm" variant="primary" loading={busy} onClick={onYes}>
          {t("rv.conn.confirm.yes")}
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={onNo}>
          {t("rv.conn.confirm.no")}
        </Button>
      </span>
    </Note>
  );
}

/** `?name=...` as a provider's return leaves it: only the known outcomes are kept. */
export function outcomeOf(search: string, name: string, known: readonly string[]): string | null {
  const value = new URLSearchParams(search).get(name);
  return known.includes(value ?? "") ? value : null;
}

export function Returned({ outcome, prefix }: { outcome: string | null; prefix: string }) {
  const { t } = useI18n();
  if (!outcome) return null;
  return <Note tone={outcome === "connected" ? "success" : "danger"}>{t(`${prefix}${outcome}` as MessageKey)}</Note>;
}
