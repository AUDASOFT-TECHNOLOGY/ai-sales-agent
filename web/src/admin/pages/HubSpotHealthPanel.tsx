/** HubSpot link health for the Admin: connection (with the guided reconnection when the client's
 *  authorisation expired), the status mapping as Revansa status -> HubSpot values, the write-back
 *  ledger counts with each problem and its action, a server-side check of the draft mapping, and
 *  "Resume synchronisation" (nothing is written twice). Owner only to act. Never a token or a contact. */

import { useCallback, useEffect, useState } from "react";
import { Button } from "../../ui/Button";
import { AdminApiError, adminCheckHubSpotMapping, adminHubSpotHealth, adminResumeHubSpotSync, type HubSpotHealth } from "../api";

export function HubSpotHealthPanel({ clientId, canEdit, draft }: { clientId: string; canEdit: boolean; draft: Record<string, unknown> | null }) {
  const [health, setHealth] = useState<HubSpotHealth | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "success" | "danger"; lines: string[] } | null>(null);

  const load = useCallback(() => {
    adminHubSpotHealth(clientId)
      .then(setHealth)
      .catch(() => setHealth(null));
  }, [clientId]);
  useEffect(load, [load]);

  async function act(work: () => Promise<{ tone: "success" | "danger"; lines: string[] }>) {
    setBusy(true);
    setNote(null);
    try {
      setNote(await work());
    } catch (err) {
      setNote({ tone: "danger", lines: [err instanceof AdminApiError ? err.message : "The request failed."] });
    } finally {
      setBusy(false);
      load();
    }
  }

  if (!health || !health.uses_hubspot) return null;
  const { connection, statuses, writeback } = health;

  return (
    <section aria-label="HubSpot link health" className="admin-llm-config">
      {connection ? (
        <div>
          <p className={`admin-alert admin-alert--${connection.state === "connected" ? "success" : "danger"}`} role={connection.state === "connected" ? "status" : "alert"}>
            {connection.message}
          </p>
          {connection.steps.length > 0 ? (
            <ol className="c-2 t-body-sm">
              {connection.steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          ) : null}
        </div>
      ) : null}

      {statuses ? (
        <div>
          {statuses.configured ? (
            <ul className="c-2 t-body-sm" aria-label="Status mapping in force">
              {Object.entries(statuses.values).map(([status, values]) => (
                <li key={status}>
                  <strong>{status}</strong>: {values.length ? values.join(", ") : "nothing mapped"}
                </li>
              ))}
            </ul>
          ) : (
            <p className="admin-alert admin-alert--danger" role="alert">{statuses.problem}</p>
          )}
          <div className="row">
            <Button
              size="sm"
              loading={busy}
              disabledReason={!canEdit ? "Only an owner can do this." : !draft ? "Fill in a valid mapping first." : undefined}
              onClick={() =>
                void act(async () => {
                  const result = await adminCheckHubSpotMapping(clientId, draft ?? {});
                  return result.ok && result.problems.length === 0
                    ? { tone: "success", lines: ["The server accepts this mapping."] }
                    : { tone: result.ok ? "success" : "danger", lines: result.problems };
                })
              }
            >
              Check with the server
            </Button>
          </div>
        </div>
      ) : null}

      {writeback ? (
        <div>
          <p className="c-2 t-body-sm">
            Synchronisation: {writeback.applied} applied, {writeback.waiting} waiting, {writeback.failed} failed, {writeback.overruled} changed by a person in HubSpot (left as they are).
          </p>
          {writeback.problems.map((p) => (
            <p key={p.code} className="admin-alert admin-alert--danger" role="alert">
              {p.message}
            </p>
          ))}
          <div className="row">
            <Button
              size="sm"
              loading={busy}
              disabledReason={!canEdit ? "Only an owner can do this." : !health.can_resume ? "Connect HubSpot and switch write-back on first." : undefined}
              onClick={() =>
                void act(async () => {
                  const done = await adminResumeHubSpotSync(clientId);
                  return { tone: done.ran && done.problems.length === 0 ? "success" : "danger", lines: [done.message, ...done.problems] };
                })
              }
            >
              Resume synchronisation
            </Button>
          </div>
        </div>
      ) : null}

      {note ? (
        <div className={`admin-alert admin-alert--${note.tone}`} role={note.tone === "danger" ? "alert" : "status"}>
          {note.lines.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </div>
      ) : null}
    </section>
  );
}
