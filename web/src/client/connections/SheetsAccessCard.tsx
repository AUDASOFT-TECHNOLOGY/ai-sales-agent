/** Connect the client's own Google Sheet: the client has saved the link, clicks "Authorise Revansa",
 *  agrees on Google's own page and comes back. No file, no service account, no address to share. The
 *  address shown is the client's own Google account. */

import { useEffect, useState } from "react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { Chip } from "../../ui/Chip";
import { clientSheetsAccess, clientSheetsAccessDisconnect, clientSheetsAccessStart, type SheetsAccess } from "../api";
import { Card, Note } from "../kit";

const RETURNS = ["connected", "denied", "scope", "invalid", "unavailable", "error"] as const;
type GoogleReturn = (typeof RETURNS)[number];

/** `?google_sheets=...` as Google's return leaves it. Anything else is ignored. */
export function sheetsReturnOf(search: string): GoogleReturn | null {
  const value = new URLSearchParams(search).get("google_sheets");
  return (RETURNS as readonly string[]).includes(value ?? "") ? (value as GoogleReturn) : null;
}

interface Props {
  /** Changes when the saved link changes, so the card asks the server again. */
  linkKey: string;
  /** Called when the connection changed, so the page can reload what it shows. */
  onChanged?: () => void;
  /** Sends the browser to Google (replaceable in tests). */
  go?: (url: string) => void;
}

export function SheetsAccessCard({ linkKey, onChanged, go }: Props) {
  const { t } = useI18n();
  const [access, setAccess] = useState<SheetsAccess | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [returned] = useState(() => sheetsReturnOf(window.location.search));

  useEffect(() => {
    let cancelled = false;
    clientSheetsAccess()
      .then((value) => !cancelled && setAccess(value))
      .catch(() => !cancelled && setAccess(null));
    return () => {
      cancelled = true;
    };
  }, [linkKey]);

  const state = access?.state ?? "not_connected";
  const connected = state === "connected" || state === "reauth_required";
  const needsLink = access !== null && !access.spreadsheet_saved;

  const authorise = () => {
    setFailed(false);
    setBusy(true);
    clientSheetsAccessStart()
      .then(({ authorize_url }) => (go ?? ((url) => window.location.assign(url)))(authorize_url))
      .catch(() => {
        setFailed(true);
        setBusy(false);
      });
  };

  const disconnect = () => {
    setFailed(false);
    setBusy(true);
    clientSheetsAccessDisconnect()
      .then((next) => {
        setAccess(next);
        onChanged?.();
      })
      .catch(() => setFailed(true))
      .finally(() => setBusy(false));
  };

  return (
    <Card aria-label={t("rv.src.google.title")}>
      <div className="rv-card__head">
        <h3>{t("rv.src.google.title")}</h3>
        <Chip tint={state === "connected" ? "success" : undefined}>{t(`rv.src.google.state.${state}` as MessageKey)}</Chip>
      </div>
      <p className="rv-card__sub">{t("rv.src.google.help")}</p>
      {returned ? <Note tone={returned === "connected" ? "success" : "danger"}>{t(`rv.src.google.return.${returned}` as MessageKey)}</Note> : null}
      {state === "unavailable" ? <Note tone="warn">{t("rv.src.google.unavailable")}</Note> : null}
      {connected && access?.address ? <p dir="auto">{t("rv.src.google.connectedAs", { address: access.address })}</p> : null}
      {failed ? <Note tone="danger">{t("rv.src.google.error")}</Note> : null}
      {state !== "unavailable" ? (
        <div className="row row--wrap">
          {state !== "connected" ? (
            <Button variant="primary" onClick={authorise} loading={busy} disabledReason={needsLink ? t("rv.src.google.saveFirst") : undefined}>
              {t(state === "reauth_required" ? "rv.src.google.reconnect" : "rv.src.google.authorise")}
            </Button>
          ) : null}
          {connected ? (
            <Button onClick={disconnect} loading={busy}>
              {t("rv.src.google.disconnect")}
            </Button>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}
