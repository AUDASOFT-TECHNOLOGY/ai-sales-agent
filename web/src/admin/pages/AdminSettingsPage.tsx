/** Read-only platform LLM configuration: GET /admin/v1/settings/llm (see
 *  src/sales_app/api/admin_settings.py). Never shows a secret -- only provider names and
 *  allow-listed model ids -- and there is no control here to change a provider or model. */

import { useEffect, useState } from "react";
import { Glass } from "../../ui/Glass";
import { Chip } from "../../ui/Chip";
import { AdminApiError, adminGetLLMSettings, type LLMSettings } from "../api";

type LoadState = "loading" | "ready" | "error";

export function AdminSettingsPage() {
  const [settings, setSettings] = useState<LLMSettings | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    adminGetLLMSettings()
      .then((data) => {
        if (cancelled) return;
        setSettings(data);
        setState("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof AdminApiError ? err.message : "Could not load LLM settings.");
        setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="admin-settings">
      <Glass level={1} className="admin-settings__notice" role="note">
        <p className="c-2 t-body-sm">
          Read-only. This shows the LLM configuration wired into this deployment; changing a client&apos;s provider or
          model is not available here yet.
        </p>
      </Glass>

      {state === "loading" ? (
        <p className="c-2" role="status" aria-live="polite">
          Loading LLM settings…
        </p>
      ) : null}
      {state === "error" ? (
        <p className="c-danger" role="alert">
          {error}
        </p>
      ) : null}

      {state === "ready" && settings ? (
        <Glass level={1} className="admin-settings__card">
          <div className="admin-settings__head">
            <h2 className="t-h2">LLM providers</h2>
            <Chip tint={settings.restricted ? "attention" : "neutral"}>
              {settings.restricted ? "Allow-list active" : "Unrestricted"}
            </Chip>
          </div>
          <p className="c-3 t-body-sm">
            {settings.restricted
              ? "Only the models listed below may be requested by a client."
              : "No deployment allow-list is configured, so any model is accepted from a wired provider."}
          </p>

          {settings.providers.length === 0 ? (
            <p className="c-2">No LLM provider is wired into this deployment.</p>
          ) : (
            <ul className="admin-llm-provider-list">
              {settings.providers.map((p) => (
                <li key={p.provider}>
                  <Glass level={2} className="admin-llm-provider-card">
                    <div className="admin-llm-provider-card__head">
                      <span className="t-label c-1">{p.provider}</span>
                      <Chip tint={p.wired ? "success" : "neutral"}>{p.wired ? "Wired" : "Not wired"}</Chip>
                    </div>
                    <div className="admin-llm-provider-card__models">
                      <span className="c-3 t-body-sm">Allowed models</span>
                      {p.allowed_models === null ? (
                        <span className="c-2">Any</span>
                      ) : p.allowed_models.length === 0 ? (
                        <span className="c-2">None</span>
                      ) : (
                        <div className="admin-settings__models">
                          {p.allowed_models.map((model) => (
                            <span key={model} className="admin-provider-pill">
                              {model}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </Glass>
                </li>
              ))}
            </ul>
          )}
        </Glass>
      ) : null}
    </div>
  );
}
