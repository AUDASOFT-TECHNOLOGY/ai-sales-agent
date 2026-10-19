/** Client sheet → the client's own Telegram account (Admin only).
 *
 *  The client signs in with Telegram itself; the Admin never sees a phone number, a code, a password
 *  or a session. This card shows where the account stands and does the two things only the platform
 *  decides: end the connection, and lift Telegram's limit once Telegram itself has lifted it (the
 *  Admin checks with @SpamBot; Revansa never finds out by sending).
 *
 *  This component calls no API itself: the page passes the calls as props. */

import { useState } from "react";
import { Chip } from "../../ui/Chip";
import { Button } from "../../ui/Button";

export interface TelegramAccountAdminState {
  /** The platform's Telegram application and library are in place. */
  platform_configured: boolean;
  /** What the Admin still has to set (names only). */
  platform_missing: string[];
  state: "not_connected" | "connected" | "reauth_required" | "unavailable";
  /** Telegram limited the account: no first message is sent. */
  limited: boolean;
  message: string;
}

export interface AdminClientTelegramAccountCardProps {
  /** Only an owner may disconnect an account or lift a limit. */
  canEdit?: boolean;
  initial: TelegramAccountAdminState;
  onDisconnect: () => Promise<TelegramAccountAdminState>;
  onClearLimit: () => Promise<TelegramAccountAdminState>;
}

const LABEL: Record<TelegramAccountAdminState["state"], readonly [string, "neutral" | "success" | "attention"]> = {
  not_connected: ["Not connected", "neutral"],
  connected: ["Connected", "success"],
  reauth_required: ["To reconnect", "attention"],
  unavailable: ["Unavailable", "attention"],
};

export function AdminClientTelegramAccountCard({
  canEdit = true,
  initial,
  onDisconnect,
  onClearLimit,
}: AdminClientTelegramAccountCardProps) {
  const [view, setView] = useState(initial);
  const [busy, setBusy] = useState<null | "disconnect" | "limit">(null);
  const [error, setError] = useState<string | null>(null);

  async function act(kind: "disconnect" | "limit", call: () => Promise<TelegramAccountAdminState>) {
    setError(null);
    setBusy(kind);
    try {
      setView(await call());
    } catch {
      setError(kind === "disconnect" ? "Could not disconnect the account." : "Could not lift the limit.");
    } finally {
      setBusy(null);
    }
  }

  const disconnect = () => {
    if (!window.confirm("Disconnect this client's Telegram account? The agent stops writing and reading on Telegram.")) return;
    void act("disconnect", onDisconnect);
  };
  const clearLimit = () => {
    if (!window.confirm("Lift the limit? Only do it once Telegram itself says the account is free (ask @SpamBot).")) return;
    void act("limit", onClearLimit);
  };

  const [label, tint] = LABEL[view.state];
  const connected = view.state === "connected" || view.state === "reauth_required";
  const ownerOnly = !canEdit ? "Only an owner can change a client's Telegram account." : undefined;

  return (
    <div className="admin-llm-config" aria-label="Telegram account">
      <span className="admin-settings__status">
        <span className="c-3 t-body-sm">Telegram account</span>
        <Chip tint={tint}>{label}</Chip>
        {view.limited ? <Chip tint="attention">Limited by Telegram</Chip> : null}
      </span>
      <p className="c-3 t-body-sm" role="status">
        {view.message}
      </p>
      {!view.platform_configured ? (
        <p className="admin-alert admin-alert--info" role="status">
          Set the platform&apos;s Telegram application first (Settings → Integrations): {view.platform_missing.join(", ")}.
        </p>
      ) : null}
      <p className="c-3 t-body-sm">
        The client signs in with its own Telegram account from its channel page. Revansa writes first only to the people the
        client marked as expecting it, within its daily limit, and stops sending first messages as soon as Telegram limits the
        account.
      </p>
      <div className="admin-settings__actions">
        <Button
          type="button"
          variant="ghost"
          disabled={!canEdit || busy !== null || !connected}
          loading={busy === "disconnect"}
          title={ownerOnly}
          onClick={disconnect}
        >
          Disconnect account
        </Button>{" "}
        <Button
          type="button"
          variant="ghost"
          disabled={!canEdit || busy !== null || !view.limited}
          loading={busy === "limit"}
          title={ownerOnly}
          onClick={clearLimit}
        >
          Lift Telegram limit
        </Button>
      </div>
      {error ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
