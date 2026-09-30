/** Read-only client list: GET /admin/v1/clients (see src/sales_app/api/admin_clients.py).
 *  No fixture data -- every client shown here exists in `clients/<id>/client.yaml`. */

import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Glass } from "../../ui/Glass";
import { Chip } from "../../ui/Chip";
import { TextInput } from "../../ui/Controls";
import { AdminApiError, adminListClients, type ClientSummary } from "../api";

type LoadState = "loading" | "ready" | "error";

export function AdminClientsPage() {
  const [clients, setClients] = useState<ClientSummary[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    adminListClients()
      .then((data) => {
        if (cancelled) return;
        setClients(data);
        setState("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof AdminApiError ? err.message : "Could not load clients.");
        setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return clients;
    return clients.filter((c) => c.client_id.toLowerCase().includes(q) || c.display_name.toLowerCase().includes(q));
  }, [clients, query]);

  return (
    <div className="admin-clients">
      <div className="admin-clients__toolbar">
        <TextInput
          label="Search clients"
          placeholder="Search by name or client ID…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {state === "ready" ? (
          <span className="c-3 t-body-sm admin-clients__count">
            {filtered.length} of {clients.length}
          </span>
        ) : null}
      </div>

      {state === "loading" ? (
        <p className="c-2" role="status" aria-live="polite">
          Loading clients…
        </p>
      ) : null}
      {state === "error" ? (
        <p className="c-danger" role="alert">
          {error}
        </p>
      ) : null}

      {state === "ready" && filtered.length === 0 ? (
        <Glass level={1} className="admin-placeholder">
          <p className="c-2">{clients.length === 0 ? "No clients are configured yet." : "No client matches your search."}</p>
        </Glass>
      ) : null}

      {state === "ready" && filtered.length > 0 ? (
        <ul className="admin-client-list">
          {filtered.map((c) => (
            <li key={c.client_id}>
              <Glass as={Link} to={`/admin/clients/${encodeURIComponent(c.client_id)}`} level={1} className="admin-client-card">
                <div className="admin-client-card__head">
                  <span className="t-label c-1">{c.display_name}</span>
                  <Chip tint={c.status === "active" ? "success" : "neutral"}>{c.status === "active" ? "Active" : "Disabled"}</Chip>
                </div>
                <p className="c-3 t-body-sm tabular">{c.client_id}</p>
                <dl className="admin-client-card__meta">
                  <div>
                    <dt className="c-3 t-body-sm">Mode</dt>
                    <dd className="c-2">{c.mode ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="c-3 t-body-sm">Timezone</dt>
                    <dd className="c-2">{c.timezone ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="c-3 t-body-sm">Daily limit</dt>
                    <dd className="c-2 tabular">{c.daily_action_limit ?? "—"}</dd>
                  </div>
                </dl>
                {c.providers.sheets || c.providers.llm || c.providers.whatsapp ? (
                  <div className="admin-client-card__providers">
                    {[c.providers.sheets, c.providers.llm, c.providers.whatsapp]
                      .filter((p): p is string => Boolean(p))
                      .map((p) => (
                        <span key={p} className="admin-provider-pill">
                          {p}
                        </span>
                      ))}
                  </div>
                ) : null}
              </Glass>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
