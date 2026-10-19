/** A client's prospect sources, from the Admin's side (owner only to change).
 *
 *  1. "Move onto lanes": one step that lets the client run several sources, each with its own channel
 *     and products, and choose where results are copied back (POST /clients/:id/lanes/adopt).
 *  2. HubSpot: what each lead status value means to Revansa (GET/PUT /clients/:id/source). There is no
 *     default: until it is set, the agent does not run on HubSpot. The client's own write-back choices
 *     are kept when this is saved. */

import { useEffect, useState } from "react";
import { Button } from "../../ui/Button";
import { Textarea, TextInput } from "../../ui/Controls";
import { useAdminAuth } from "../AdminAuthContext";
import { AdminApiError, adminAdoptLanes, adminGetClientSource, adminSaveClientSource, type ClientSourceSettings } from "../api";

const STATUSES = ["new", "paused", "closed", "opted_out"];

/** "VALUE = status" lines as the server wants them. A line that is not one is reported. */
export function parseMapping(text: string): { mapping: Record<string, string>; bad: boolean } {
  const mapping: Record<string, string> = {};
  let bad = false;
  for (const line of text.split("\n")) {
    if (line.trim() === "") continue;
    const [left, right, ...rest] = line.split("=");
    const key = left?.trim() ?? "";
    const value = right?.trim().toLowerCase() ?? "";
    if (key === "" || !STATUSES.includes(value) || rest.length > 0) bad = true;
    else mapping[key] = value;
  }
  return { mapping, bad };
}

export function mappingToText(mapping: unknown): string {
  if (!mapping || typeof mapping !== "object") return "";
  return Object.entries(mapping as Record<string, string>)
    .map(([key, value]) => `${key} = ${value}`)
    .join("\n");
}

export function AdminClientSourcesCard({ clientId }: { clientId: string }) {
  const { admin } = useAdminAuth();
  const canEdit = admin?.role === "owner";
  const [source, setSource] = useState<ClientSourceSettings | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [mapping, setMapping] = useState("");
  const [property, setProperty] = useState("hs_lead_status");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "success" | "danger"; text: string } | null>(null);

  function adopt(next: ClientSourceSettings) {
    setSource(next);
    const settings = next.settings ?? {};
    setMapping(mappingToText(settings.status_mapping));
    setProperty(typeof settings.status_property === "string" ? settings.status_property : "hs_lead_status");
  }

  useEffect(() => {
    let cancelled = false;
    adminGetClientSource(clientId)
      .then((s) => !cancelled && adopt(s))
      .catch(() => !cancelled && setLoadError(true));
    return () => {
      cancelled = true;
    };
  }, [clientId]);

  async function run(work: () => Promise<string>, failure: string) {
    setBusy(true);
    setMessage(null);
    try {
      setMessage({ tone: "success", text: await work() });
    } catch (err) {
      setMessage({ tone: "danger", text: err instanceof AdminApiError ? err.message : failure });
    } finally {
      setBusy(false);
    }
  }

  if (loadError) return <p className="admin-alert admin-alert--danger">Could not load the client&apos;s sources.</p>;
  if (!source) return <p className="c-2 t-body-sm">Loading…</p>;

  const parsed = parseMapping(mapping);
  const hubspot = source.provider === "hubspot";

  return (
    <div className="admin-llm-config">
      <p className="c-2 t-body-sm">
        Several sources, each with its own channel and products, and the results copied back into the client&apos;s sheet or
        HubSpot, need the client to run lanes. This moves the client&apos;s current source and channel into a first lane; the
        client manages its lanes itself afterwards. The agent is paused.
      </p>
      <div className="row">
        <Button
          size="sm"
          loading={busy}
          disabledReason={!canEdit ? "Only an owner can do this." : undefined}
          onClick={() =>
            void run(async () => {
              const done = await adminAdoptLanes(clientId);
              return `Done: lane “${done.lane}” on ${done.source}, channel ${done.channel}.`;
            }, "Could not move the client onto lanes.")
          }
        >
          Move onto lanes
        </Button>
      </div>

      {hubspot ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (parsed.bad || Object.keys(parsed.mapping).length === 0) {
              setMessage({ tone: "danger", text: "Each line must look like HUBSPOT_VALUE = new, paused, closed or opted_out." });
              return;
            }
            void run(async () => {
              const base = { ...(source.settings ?? {}) } as Record<string, unknown>;
              const saved = await adminSaveClientSource(clientId, { ...base, status_property: property.trim() || "hs_lead_status", status_mapping: parsed.mapping });
              adopt(saved);
              return "HubSpot statuses saved.";
            }, "Could not save the HubSpot statuses.");
          }}
          aria-label="HubSpot statuses"
        >
          <p className="c-2 t-body-sm">
            What each HubSpot lead status means. Only contacts with a listed status are contacted; a blank or unlisted status is
            left out. {source.configured ? "" : "Not set yet: the agent cannot run on HubSpot."}
          </p>
          <TextInput label="Status property" value={property} onChange={(e) => setProperty(e.target.value)} disabled={!canEdit} dir="ltr" />
          <Textarea
            label="Meaning of each value"
            helper="One per line: HUBSPOT_VALUE = new, paused, closed or opted_out. At least one must be new."
            rows={5}
            value={mapping}
            onChange={(e) => setMapping(e.target.value)}
            disabled={!canEdit}
            dir="ltr"
            spellCheck={false}
          />
          <div className="row">
            <Button type="submit" variant="primary" size="sm" loading={busy} disabledReason={!canEdit ? "Only an owner decides what the CRM means." : undefined}>
              Save statuses
            </Button>
          </div>
        </form>
      ) : (
        <p className="c-3 t-body-sm">The HubSpot statuses are set here once the client has a HubSpot source.</p>
      )}

      {message ? (
        <p className={`admin-alert admin-alert--${message.tone}`} role={message.tone === "danger" ? "alert" : "status"}>
          {message.text}
        </p>
      ) : null}
    </div>
  );
}
