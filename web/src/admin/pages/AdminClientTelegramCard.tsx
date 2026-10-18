/** Client sheet → Telegram bot (Admin only).
 *
 *  The administrator pastes the client's bot token. It is write-only: once saved it is never shown
 *  again, only "Configured". Replacing a token = saving the new one, which reconnects the bot. Connect
 *  tells Telegram to deliver the bot's messages to the platform. Test only asks Telegram who the bot is
 *  and where it delivers: it never messages a prospect. Prospects must start the bot through a Telegram
 *  invitation link.
 *
 *  This component calls no API itself: the integration wave passes the calls as props.
 *
 *  Backend contracts (see the integration notes): token save = existing
 *  `PUT /admin/v1/clients/{id}/integrations/telegram.bot_token`; connect = existing
 *  `POST .../integrations/telegram/connect`; test (read-only) and disconnect are MISSING routes. */

import { useId, useState } from "react";
import { Chip } from "../../ui/Chip";
import { Button } from "../../ui/Button";
import { FieldShell } from "../../ui/Controls";

export interface TelegramConnectionTest {
  ok: boolean;
  message: string;
  details?: Record<string, string>;
}

/** What the card needs from a failed call. `AdminApiError` fits this shape. */
export interface TelegramCallError {
  status?: number;
  message: string;
}

export interface AdminClientTelegramCardProps {
  clientId: string;
  /** Only an owner may change a client's bot. */
  canEdit?: boolean;
  /** Whether a bot token is stored (the value itself is never provided). */
  tokenConfigured: boolean;
  /** Whether the bot is connected to the platform, when known. */
  connected?: boolean | null;
  onSaveToken: (token: string) => Promise<unknown>;
  onConnect: () => Promise<TelegramConnectionTest>;
  /** Read-only check: must never message any prospect. */
  onTest: () => Promise<TelegramConnectionTest>;
  onDisconnect: () => Promise<unknown>;
}

/** Non-technical wording for the refusals the connect route answers with 409. No secret detail. */
export function telegramErrorText(err: unknown, fallback: string): string {
  const e = err as Partial<TelegramCallError> | null;
  const message = typeof e?.message === "string" ? e.message : "";
  if (e?.status === 409) {
    if (/no Telegram bot token/i.test(message)) return "No bot token is saved for this client yet. Paste the token first.";
    if (/public https address/i.test(message))
      return "The platform has no public https address yet, so Telegram cannot reach it. Ask the platform team to set it up.";
    if (/cannot be used for this account/i.test(message)) return "This bot is already used by another client. Use a different bot.";
    return "The bot cannot be connected right now.";
  }
  return fallback;
}

export function AdminClientTelegramCard({
  canEdit = true,
  tokenConfigured,
  connected = null,
  onSaveToken,
  onConnect,
  onTest,
  onDisconnect,
}: AdminClientTelegramCardProps) {
  const id = useId();
  const [configured, setConfigured] = useState(tokenConfigured);
  const [isConnected, setIsConnected] = useState<boolean | null>(connected);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState<null | "save" | "connect" | "test" | "disconnect">(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ kind: "connect" | "test"; value: TelegramConnectionTest } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Follow the props when they change (set during render, not in an effect).
  const [seenToken, setSeenToken] = useState(tokenConfigured);
  if (seenToken !== tokenConfigured) {
    setSeenToken(tokenConfigured);
    setConfigured(tokenConfigured);
  }
  const [seenConnected, setSeenConnected] = useState(connected);
  if (seenConnected !== connected) {
    setSeenConnected(connected);
    setIsConnected(connected);
  }

  const reset = () => {
    setError(null);
    setResult(null);
    setNotice(null);
  };

  async function connect() {
    reset();
    setBusy("connect");
    try {
      const value = await onConnect();
      setResult({ kind: "connect", value });
      setIsConnected(value.ok);
    } catch (err) {
      setError(telegramErrorText(err, "Could not connect the bot."));
    } finally {
      setBusy(null);
    }
  }

  async function saveAndConnect() {
    const token = draft.trim();
    if (!token) return;
    reset();
    setBusy("save");
    try {
      await onSaveToken(token);
    } catch (err) {
      setError(telegramErrorText(err, "Could not save the token."));
      setBusy(null);
      return;
    }
    setDraft(""); // write-only: the token leaves the screen as soon as it is stored
    setConfigured(true);
    setNotice("Token saved.");
    setBusy("connect");
    try {
      const value = await onConnect();
      setResult({ kind: "connect", value });
      setIsConnected(value.ok);
    } catch (err) {
      setIsConnected(false);
      setError(telegramErrorText(err, "The token is saved, but the bot could not be connected."));
    } finally {
      setBusy(null);
    }
  }

  async function test() {
    reset();
    setBusy("test");
    try {
      setResult({ kind: "test", value: await onTest() });
    } catch (err) {
      setError(telegramErrorText(err, "The test could not be run."));
    } finally {
      setBusy(null);
    }
  }

  async function disconnect() {
    if (!window.confirm("Disconnect this client's Telegram bot? Prospects will no longer reach the agent on Telegram.")) return;
    reset();
    setBusy("disconnect");
    try {
      await onDisconnect();
      setIsConnected(false);
      setNotice("Bot disconnected.");
    } catch (err) {
      setError(telegramErrorText(err, "Could not disconnect the bot."));
    } finally {
      setBusy(null);
    }
  }

  const ownerOnly = !canEdit ? "Only an owner can change a client's Telegram bot." : undefined;
  const [label, tint] = !configured
    ? (["Not configured", "neutral"] as const)
    : isConnected
      ? (["Connected", "success"] as const)
      : (["Configured, not connected", "attention"] as const);

  return (
    <div className="admin-llm-config">
      <span className="admin-settings__status">
        <span className="c-3 t-body-sm">Telegram status</span>
        <Chip tint={tint}>{label}</Chip>
      </span>

      <FieldShell
        id={id}
        label="Bot token"
        helper={
          configured
            ? "A token is stored and never shown again. Saving a new token replaces it and reconnects the bot."
            : "Paste the token given by BotFather for this client's bot. Stored encrypted. Never shown again once saved."
        }
        badge={<Chip tint={configured ? "success" : "neutral"}>{configured ? "Configured" : "Not set"}</Chip>}
      >
        <div className="admin-settings__row">
          <input
            id={id}
            className="input well"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={draft}
            placeholder={configured ? "Configured — enter a new token to replace it" : "Bot token"}
            disabled={!canEdit || busy !== null}
            onChange={(e) => setDraft(e.target.value)}
          />
          <Button
            variant="primary"
            size="sm"
            loading={busy === "save"}
            disabled={draft.trim() === "" || busy !== null || !canEdit}
            disabledReason={ownerOnly}
            onClick={saveAndConnect}
          >
            {configured ? "Replace token" : "Save token"}
          </Button>
        </div>
      </FieldShell>

      <div className="admin-llm-provider-card__models">
        <div className="admin-settings__row">
          <Button
            variant="secondary"
            size="sm"
            loading={busy === "connect"}
            disabled={!canEdit || !configured || busy !== null}
            disabledReason={ownerOnly ?? (!configured ? "Save a bot token first." : undefined)}
            onClick={connect}
          >
            Connect
          </Button>
          <Button
            variant="secondary"
            size="sm"
            loading={busy === "test"}
            disabled={!canEdit || !configured || busy !== null}
            disabledReason={ownerOnly ?? (!configured ? "Save a bot token first." : undefined)}
            onClick={test}
          >
            Test bot
          </Button>
          <Button
            variant="ghost"
            size="sm"
            loading={busy === "disconnect"}
            disabled={!canEdit || !configured || busy !== null}
            disabledReason={ownerOnly ?? (!configured ? "No bot to disconnect." : undefined)}
            onClick={disconnect}
          >
            Disconnect
          </Button>
        </div>
        <p className="c-3 t-body-sm">
          Test only asks Telegram who the bot is and where it delivers. No message is sent to any prospect.
        </p>
        <p className="c-3 t-body-sm">
          Prospects cannot find the bot on their own: each prospect must start it through a Telegram invitation link.
        </p>
      </div>

      {notice ? (
        <p className="admin-alert admin-alert--success" role="status">
          {notice}
        </p>
      ) : null}
      {error ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {error}
        </p>
      ) : null}
      {result ? (
        <div role="status">
          <p className={result.value.ok ? "admin-alert admin-alert--success" : "admin-alert admin-alert--danger"}>
            {result.kind === "connect"
              ? result.value.ok
                ? "Connected. "
                : "Connection failed. "
              : result.value.ok
                ? "Test passed. "
                : "Test failed. "}
            {result.value.message}
          </p>
        </div>
      ) : null}
    </div>
  );
}
