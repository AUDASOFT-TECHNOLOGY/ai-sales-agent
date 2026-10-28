/** "A connection is needed": what the circuits and the prospect lists show, instead of a connector's own
 *  settings, when the source or the channel they use is not connected yet. The connections themselves
 *  are made on the Connections screen only. */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useI18n } from "../../i18n";
import { clientHubSpotAccess, clientSheetsAccess } from "../api";
import { Note } from "../kit";

export function ConnectionNeeded({ text, platform }: { text?: string; platform?: boolean }) {
  const { t } = useI18n();
  return (
    <Note tone="warn" title={t("rv.conn.needed.title")}>
      <span dir="auto">{text ?? t(platform ? "rv.conn.needed.platform" : "rv.conn.needed.text")}</span>{" "}
      <Link to="/connections" className="link-btn">
        {t("rv.conn.needed.link")}
      </Link>
    </Note>
  );
}

type Access = { state: "not_connected" | "connected" | "reauth_required" | "unavailable" };

/** Shown under a source that needs the client's own account (HubSpot, Google Sheets) until it is connected.
 *  Google Sheets shared with a service account by hand (`shareWith`) is not a connection to make here. */
export function SourceConnectionNeeded({ provider, shareWith = null }: { provider: string; shareWith?: string | null }) {
  const { t } = useI18n();
  const [access, setAccess] = useState<Access | null>(null);
  const watched = provider === "hubspot" || (provider === "google_sheets" && !shareWith);
  useEffect(() => {
    if (!watched) return;
    let cancelled = false;
    (provider === "hubspot" ? clientHubSpotAccess() : clientSheetsAccess()).then(
      (value) => !cancelled && setAccess(value),
      () => !cancelled && setAccess(null), // unreadable: say nothing rather than guess
    );
    return () => {
      cancelled = true;
    };
  }, [provider, watched]);
  if (!watched || !access || access.state === "connected") return null;
  const unavailable = access.state === "unavailable";
  return <ConnectionNeeded platform={unavailable} text={unavailable ? undefined : t(provider === "hubspot" ? "rv.conn.needed.hubspot" : "rv.conn.needed.sheets")} />;
}
