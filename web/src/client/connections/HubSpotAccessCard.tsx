/** Connect the client's own HubSpot account: the client clicks "Connect HubSpot", agrees on HubSpot's
 *  own page and comes back. No Private App, no token to copy. The account shown is the client's own. */

import { useEffect, useState } from "react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { Chip } from "../../ui/Chip";
import { clientHubSpotAccess, clientHubSpotAccessDisconnect, clientHubSpotAccessStart, type HubSpotAccess } from "../api";
import { Card, Note } from "../kit";

const RETURNS = ["connected", "denied", "scope", "invalid", "unavailable", "error"] as const;
type HubSpotReturn = (typeof RETURNS)[number];

/** `?hubspot=...` as HubSpot's return leaves it. Anything else is ignored. */
export function hubspotReturnOf(search: string): HubSpotReturn | null {
  const value = new URLSearchParams(search).get("hubspot");
  return (RETURNS as readonly string[]).includes(value ?? "") ? (value as HubSpotReturn) : null;
}

interface Props {
  /** Called when the connection changed, so the page can reload what it shows. */
  onChanged?: () => void;
  /** Sends the browser to HubSpot (replaceable in tests). */
  go?: (url: string) => void;
}

export function HubSpotAccessCard({ onChanged, go }: Props) {
  const { t } = useI18n();
  const [access, setAccess] = useState<HubSpotAccess | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [returned] = useState(() => hubspotReturnOf(window.location.search));

  useEffect(() => {
    let cancelled = false;
    clientHubSpotAccess()
      .then((value) => !cancelled && setAccess(value))
      .catch(() => !cancelled && setAccess(null));
    return () => {
      cancelled = true;
    };
  }, []);

  const state = access?.state ?? "not_connected";
  const connected = state === "connected" || state === "reauth_required";

  const authorise = () => {
    setFailed(false);
    setBusy(true);
    clientHubSpotAccessStart()
      .then(({ authorize_url }) => (go ?? ((url) => window.location.assign(url)))(authorize_url))
      .catch(() => {
        setFailed(true);
        setBusy(false);
      });
  };

  const disconnect = () => {
    setFailed(false);
    setBusy(true);
    clientHubSpotAccessDisconnect()
      .then((next) => {
        setAccess(next);
        onChanged?.();
      })
      .catch(() => setFailed(true))
      .finally(() => setBusy(false));
  };

  return (
    <Card aria-label={t("rv.src.hubspot.title")}>
      <div className="rv-card__head">
        <h3>{t("rv.src.hubspot.title")}</h3>
        <Chip tint={state === "connected" ? "success" : undefined}>{t(`rv.src.hubspot.state.${state}` as MessageKey)}</Chip>
      </div>
      <p className="rv-card__sub">{t("rv.src.hubspot.connectHelp")}</p>
      {returned ? <Note tone={returned === "connected" ? "success" : "danger"}>{t(`rv.src.hubspot.return.${returned}` as MessageKey)}</Note> : null}
      {state === "unavailable" ? <Note tone="warn">{t("rv.src.hubspot.unavailable")}</Note> : null}
      {connected && access?.account ? <p dir="auto">{t("rv.src.hubspot.connectedAs", { account: access.account })}</p> : null}
      {failed ? <Note tone="danger">{t("rv.src.hubspot.error")}</Note> : null}
      {state !== "unavailable" ? (
        <div className="row row--wrap">
          {state !== "connected" ? (
            <Button variant="primary" onClick={authorise} loading={busy}>
              {t(state === "reauth_required" ? "rv.src.hubspot.reconnect" : "rv.src.hubspot.authorise")}
            </Button>
          ) : null}
          {connected ? (
            <Button onClick={disconnect} loading={busy}>
              {t("rv.src.hubspot.disconnect")}
            </Button>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}
