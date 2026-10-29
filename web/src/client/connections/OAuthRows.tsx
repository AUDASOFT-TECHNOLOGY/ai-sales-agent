/** The three connections made on the provider's own page: the client's Gmail, its HubSpot and its Google
 *  Drive (for its Google Sheets). The client types no password and copies no key: it names the account if
 *  it wishes, then clicks "Connect my ..." and gives its permission on the provider's page. */

import type { ReactNode } from "react";
import { useI18n } from "../../i18n";
import { Button } from "../../ui/Button";
import {
  clientGmailDisconnect,
  clientGmailStart,
  clientHubSpotAccessDisconnect,
  clientHubSpotAccessStart,
  clientSaveChannel,
  clientSheetsAccessDisconnect,
  clientSheetsAccessStart,
  type ChannelState,
} from "../api";
import { ClientMailboxCard } from "../ClientMailboxCard";
import { BrandIcon, type Brand } from "./BrandIcons";
import { ConnectForm } from "./ConnectForm";
import { ConnectionRow } from "./ConnectionRow";
import { DisconnectConfirm, Returned, RowError, goTo, useRowState } from "./connectionKit";
import type { ConnectionView } from "./connectionModel";
import { EmailActivation } from "./EmailActivation";
import { SheetsConnect } from "./SheetsConnect";

interface Common {
  view: ConnectionView;
  /** The provider's outcome left in the address by a return (`connected`, `denied`...). */
  returned: string | null;
  onChanged: () => Promise<void> | void;
  /** Sends the browser to the provider (replaceable in tests). */
  go?: (url: string) => void;
}

/** A row connected through the provider's page and disconnected with one confirmation. */
function ProviderRow({
  id,
  brand,
  name,
  view,
  returnPrefix,
  returned,
  start,
  disconnect,
  onChanged,
  go = goTo,
  showForm = true,
  override,
  children,
}: Common & {
  id: "email" | "hubspot" | "sheets";
  brand: Brand;
  name: string;
  returnPrefix: string;
  start: () => Promise<{ authorize_url: string }>;
  disconnect: () => Promise<unknown>;
  /** False when something else connects this row (the mailbox Revansa provides). */
  showForm?: boolean;
  override?: ReactNode;
  children?: ReactNode;
}) {
  const { t } = useI18n();
  const s = useRowState();
  const connected = view.standing === "connected";
  return (
    <ConnectionRow
      id={id}
      icon={<BrandIcon brand={brand} size={28} />}
      name={name}
      standing={view.standing}
      reconnect={view.reconnect}
      forceOpen={!!returned || !!s.error}
      detail={t(view.detail, view.vars)}
      action={
        override ??
        (connected || view.reconnect ? (
          <Button variant="ghost" disabled={s.busy} onClick={() => s.setConfirming(true)}>
            {t("rv.conn.act.disconnect")}
          </Button>
        ) : null)
      }
    >
      <Returned outcome={returned} prefix={returnPrefix} />
      {s.confirming ? (
        <DisconnectConfirm
          busy={s.busy}
          onNo={() => s.setConfirming(false)}
          onYes={() =>
            void s.run(disconnect, async () => {
              s.setConfirming(false);
              await onChanged();
            })
          }
        />
      ) : null}
      {showForm && !connected && view.field ? (
        <ConnectForm
          id={id}
          field={view.field}
          brand={brand}
          value={view.value}
          reconnect={view.reconnect}
          pending={view.pending}
          required={false}
          onChanged={onChanged}
          onConnect={async () => go((await start()).authorize_url)}
        />
      ) : null}
      {children}
      <RowError error={s.error} />
    </ConnectionRow>
  );
}

export function EmailRow({ view, channel, returned, onChanged, go }: Common & { channel: ChannelState | null }) {
  const { t } = useI18n();
  const s = useRowState();
  // The mailbox Revansa provides excludes the client's Gmail: the client chooses to leave it first.
  const provided = view.provided === true;
  const leave =
    provided && channel?.email?.mode === "platform" ? (
      <Button
        variant="ghost"
        loading={s.busy}
        onClick={() => void s.run(() => clientSaveChannel({ expected_version: channel.version, channel: "email", email: { use_gmail: true } }), onChanged)}
      >
        {t("rv.conn.act.useGmail")}
      </Button>
    ) : undefined;
  return (
    <ProviderRow
      id="email"
      brand="gmail"
      name={t("rv.conn.name.email")}
      view={view}
      returnPrefix="channel.gmail.return."
      returned={returned}
      start={clientGmailStart}
      disconnect={clientGmailDisconnect}
      onChanged={onChanged}
      go={go}
      showForm={!provided}
      override={leave}
    >
      {provided && channel ? <EmailActivation channel={channel} onChanged={onChanged} /> : null}
      {provided && channel?.email?.mode === "platform" ? <ClientMailboxCard showNotRequested /> : null}
      <RowError error={s.error} />
    </ProviderRow>
  );
}

export function HubSpotRow({ view, ...rest }: Common) {
  const { t } = useI18n();
  return (
    <ProviderRow
      {...rest}
      view={view}
      id="hubspot"
      brand="hubspot"
      name={t("rv.conn.name.hubspot")}
      returnPrefix="rv.src.hubspot.return."
      start={clientHubSpotAccessStart}
      disconnect={clientHubSpotAccessDisconnect}
    />
  );
}

export function SheetsRow({ view, shareWith, ...rest }: Common & { shareWith: string | null }) {
  const { t } = useI18n();
  return (
    <ProviderRow
      {...rest}
      view={view}
      id="sheets"
      brand="drive"
      name={t("rv.conn.name.sheets")}
      returnPrefix="rv.src.google.return."
      start={clientSheetsAccessStart}
      disconnect={clientSheetsAccessDisconnect}
      showForm={!shareWith}
    >
      {shareWith ? <SheetsConnect shareWith={shareWith} /> : null}
    </ProviderRow>
  );
}
