/** How a client connects one service: the account it wants (when that helps), then one button named for
 *  the service and carrying its mark: "Connect my Gmail", "Connect my HubSpot", "Connect my Google Drive"...
 *
 *  An address or a number alone does not connect anything: the owner of the account must give its
 *  permission on the provider's own page (or type the code the provider sends). So the button always
 *  leads there. What the client types is kept first, so that nothing is lost; and when the provider's
 *  page cannot open yet, the client is told its request is kept and that it will be asked to authorise.
 *  It is never told why, and never blocked: its side does not depend on Revansa's. */

import { useState, type FormEvent } from "react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { TextInput } from "../../ui/Controls";
import { ClientApiError, clientSetHint } from "../api";
import { useClientAuth } from "../ClientAuthContext";
import { Note } from "../kit";
import { BrandIcon, type Brand } from "./BrandIcons";
import { errorKeyOf } from "./connectionKit";
import type { ConnectionId, Field } from "./connectionModel";

const INPUT: Record<Field, { type: "email" | "tel"; inputMode: "email" | "tel"; autoComplete: string }> = {
  gmail: { type: "email", inputMode: "email", autoComplete: "email" },
  sheets: { type: "email", inputMode: "email", autoComplete: "email" },
  hubspot: { type: "email", inputMode: "email", autoComplete: "email" },
  telegram: { type: "tel", inputMode: "tel", autoComplete: "tel" },
  whatsapp: { type: "tel", inputMode: "tel", autoComplete: "tel" },
};

interface Props {
  id: ConnectionId;
  field: Field;
  brand: Brand;
  /** What the client stated before. */
  value: string | null;
  reconnect: boolean;
  /** The provider's page cannot open yet: the request is kept, nothing else happens. */
  pending: boolean;
  /** The account must be stated to go on (a number, a phone); else it only helps the provider's page. */
  required: boolean;
  /** The step after the account is kept, when the provider's page can open (redirect, window, code). */
  onConnect: (value: string) => Promise<void>;
  onChanged: () => Promise<void> | void;
}

export function ConnectForm({ id, field, brand, value, reconnect, pending, required, onConnect, onChanged }: Props) {
  const { t } = useI18n();
  const { updateProfile } = useClientAuth();
  const [draft, setDraft] = useState(value ?? "");
  // What the server keeps replaces what was typed once it has changed (a reload, a number saved elsewhere).
  const [seen, setSeen] = useState(value);
  if (value !== seen) {
    setSeen(value);
    setDraft(value ?? "");
  }
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<MessageKey | null>(null);
  const [registered, setRegistered] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = draft.trim();
    setError(null);
    setRegistered(false);
    if (text === "" && (required || pending)) {
      setError("rv.conn.field.required");
      return;
    }
    setBusy(true);
    try {
      if (text !== "" && text !== (value ?? "")) {
        // The WhatsApp number is the client's profile; every other account is kept as a hint.
        if (field === "whatsapp") await updateProfile({ whatsapp_number: text });
        else await clientSetHint(field, text);
      }
      if (pending) {
        setRegistered(true);
        await onChanged();
      } else {
        await onConnect(text);
      }
    } catch (err) {
      setError(err instanceof ClientApiError && (err.status === 422 || err.status === 400) ? "rv.conn.field.invalid" : errorKeyOf(err));
    }
    setBusy(false);
  }

  return (
    <form className="stack" onSubmit={(e) => void submit(e)} noValidate>
      <TextInput
        label={t(`rv.conn.field.${field}` as MessageKey)}
        helper={t(`rv.conn.field.help.${field}` as MessageKey)}
        optional={required || pending ? undefined : t("rv.conn.field.optional")}
        name={`account-${id}`}
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          setRegistered(false);
        }}
        error={error === "rv.conn.field.invalid" || error === "rv.conn.field.required" ? t(error) : undefined}
        disabled={busy}
        maxLength={80}
        dir="ltr"
        {...INPUT[field]}
      />
      <div className="rv-actions">
        <Button type="submit" variant="primary" size="lg" loading={busy} leading={<BrandIcon brand={brand} />}>
          {t(`rv.conn.btn.${reconnect ? "re." : ""}${id}` as MessageKey)}
        </Button>
      </div>
      {registered ? <Note tone="success">{t("rv.conn.registered")}</Note> : null}
      {error && error !== "rv.conn.field.invalid" && error !== "rv.conn.field.required" ? <Note tone="danger">{t(error)}</Note> : null}
    </form>
  );
}
