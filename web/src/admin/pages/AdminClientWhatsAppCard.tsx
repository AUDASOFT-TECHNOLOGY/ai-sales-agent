/** One client's WhatsApp account, for the Admin: where the connection stands (as Revansa stored it,
 *  and "Ask Meta now" to check it), the message templates and which one opens conversations, recent
 *  delivery failures, and the way out. The platform's Meta setup (what is still to enter, the
 *  addresses to give Meta) is shown beside it. No token, PIN or secret is ever shown. */

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Button } from "../../ui/Button";
import {
  WhatsAppApiError,
  adminWhatsAppAccount,
  adminWhatsAppCreateTemplate,
  adminWhatsAppDeleteTemplate,
  adminWhatsAppDisconnect,
  adminWhatsAppPlatform,
  adminWhatsAppRefresh,
  adminWhatsAppSetOpening,
  adminWhatsAppSync,
  adminWhatsAppTemplates,
  type AdminWhatsAppAccount,
  type AdminWhatsAppPlatform,
  type AdminWhatsAppTemplate,
} from "../../whatsapp/api";
import { whatsappText, type WhatsAppKey } from "../../whatsapp/copy";

interface Props {
  clientId: string;
  /** Only an owner may ask Meta, change templates or disconnect: the routes are owner-only. */
  canEdit: boolean;
}

export function AdminClientWhatsAppCard({ clientId, canEdit }: Props) {
  // The Admin space is in English throughout (it has no language switch).
  const w = useCallback((key: WhatsAppKey, vars?: Record<string, string | number>) => whatsappText("en", key, vars), []);
  const [account, setAccount] = useState<AdminWhatsAppAccount | null>(null);
  const [platform, setPlatform] = useState<AdminWhatsAppPlatform | null>(null);
  const [templates, setTemplates] = useState<AdminWhatsAppTemplate[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [steps, setSteps] = useState<Record<string, string> | null>(null);
  const [release, setRelease] = useState(false);
  const [form, setForm] = useState({ name: "", language: "fr", category: "UTILITY", body: "", examples: "" });

  const load = useCallback(() => {
    Promise.all([
      adminWhatsAppAccount(clientId),
      adminWhatsAppPlatform(),
      adminWhatsAppTemplates(clientId).catch(() => [] as AdminWhatsAppTemplate[]),
    ]).then(
      ([a, p, t]) => {
        setAccount(a);
        setPlatform(p);
        setTemplates(t);
      },
      () => setError(w("admin.error")),
    );
  }, [clientId, w]);

  useEffect(() => load(), [load]);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof WhatsAppApiError ? err.message : w("admin.error"));
    } finally {
      setBusy(false);
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      await adminWhatsAppCreateTemplate(clientId, {
        name: form.name.trim(),
        language: form.language.trim(),
        category: form.category,
        body: form.body,
        examples: form.examples.split("\n").map((line) => line.trim()).filter(Boolean),
      });
      setTemplates(await adminWhatsAppTemplates(clientId));
      setForm({ ...form, name: "", body: "", examples: "" });
    });
  };

  if (!account || !platform) {
    return error ? (
      <p className="admin-alert admin-alert--danger" role="alert">
        {error}
      </p>
    ) : (
      <p className="c-3 t-body-sm">Loading…</p>
    );
  }
  const connected = account.state === "connected" || account.state === "reauth_required";
  const row = (label: WhatsAppKey, value: string | null | undefined) =>
    value ? (
      <p className="t-body-sm">
        <span className="c-3">{w(label)}: </span>
        {value}
      </p>
    ) : null;
  const counts = (record: Record<string, number>) =>
    Object.entries(record)
      .map(([name, n]) => `${name} ${n}`)
      .join(", ");

  return (
    <div data-testid="admin-whatsapp">
      <h4 className="t-label c-2">{w("admin.platform")}</h4>
      {platform.ready ? (
        <p className="c-success t-body-sm">{w("admin.platformReady")}</p>
      ) : (
        <p className="admin-alert admin-alert--info" role="status">
          {w("admin.platformMissing", { list: platform.missing.join(", ") })}
        </p>
      )}
      {row("admin.webhookUrl", platform.webhook_url)}
      {row("admin.deauthUrl", platform.deauthorize_url)}
      {row("admin.deletionUrl", platform.data_deletion_url)}

      <h4 className="t-label c-2">{w("admin.title")}</h4>
      {row("admin.state", account.state)}
      {row("admin.number", account.display_number)}
      {row("admin.verified", account.verified_name)}
      {row("admin.quality", account.quality)}
      {row("admin.waba", account.waba_id)}
      {row("admin.phoneId", account.phone_number_id)}
      {row("admin.connectedAt", account.connected_at)}
      <p className="t-body-sm">
        <span className="c-3">{w("admin.opening")}: </span>
        {account.opening_template ?? w("admin.noOpening")}
      </p>
      {Object.keys(account.deliveries_7d).length > 0 ? row("admin.deliveries", counts(account.deliveries_7d)) : null}
      {account.recent_failures.length > 0 ? (
        <>
          <p className="t-body-sm c-3">{w("admin.failures")}</p>
          <ul className="t-body-sm">
            {account.recent_failures.map((f) => (
              <li key={f.message_id}>
                {f.at} — {f.error_code} {f.error_title}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {error ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {error}
        </p>
      ) : null}
      {steps ? (
        <p className="admin-alert admin-alert--info" role="status">
          {w("admin.disconnectDone")} {Object.entries(steps).map(([k, v]) => `${k}: ${v}`).join(", ")}
        </p>
      ) : null}

      {canEdit && connected ? (
        <div>
          <Button
            type="button"
            variant="secondary"
            disabled={busy}
            onClick={() => void run(async () => setAccount(await adminWhatsAppRefresh(clientId)))}
          >
            {w("admin.refresh")}
          </Button>{" "}
          <label className="t-body-sm">
            <input type="checkbox" checked={release} onChange={(e) => setRelease(e.target.checked)} />{" "}
            {w("admin.releaseNumber")}
          </label>{" "}
          <Button
            type="button"
            variant="danger"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                setSteps((await adminWhatsAppDisconnect(clientId, release)).steps);
                load();
              })
            }
          >
            {w("admin.disconnect")}
          </Button>
        </div>
      ) : null}

      {connected ? (
        <>
          <h4 className="t-label c-2">{w("admin.templates")}</h4>
          {canEdit ? (
            <Button
              type="button"
              variant="secondary"
              disabled={busy}
              onClick={() => void run(async () => setTemplates(await adminWhatsAppSync(clientId)))}
            >
              {w("admin.sync")}
            </Button>
          ) : null}
          <ul className="t-body-sm" data-testid="admin-whatsapp-templates">
            {templates.map((t) => (
              <li key={`${t.name}/${t.language}`}>
                <strong>{t.name}</strong> ({t.language}, {t.category}) — {t.status}
                {t.opening ? " ★" : ""}
                {t.reason ? ` — ${t.reason}` : ""}
                <br />
                <span className="c-3">{t.body}</span>
                {canEdit ? (
                  <>
                    {" "}
                    {t.usable_as_opening && !t.opening ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        disabled={busy}
                        onClick={() =>
                          void run(async () => {
                            setAccount(await adminWhatsAppSetOpening(clientId, t.name, t.language));
                            setTemplates(await adminWhatsAppTemplates(clientId));
                          })
                        }
                      >
                        {w("admin.useOpening")}
                      </Button>
                    ) : null}{" "}
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          await adminWhatsAppDeleteTemplate(clientId, t.name);
                          setTemplates(await adminWhatsAppTemplates(clientId));
                        })
                      }
                    >
                      {w("admin.delete")}
                    </Button>
                  </>
                ) : null}
              </li>
            ))}
          </ul>
          {canEdit ? (
            <form onSubmit={submit} aria-label={w("admin.createTitle")}>
              <h5 className="t-label c-3">{w("admin.createTitle")}</h5>
              <input
                aria-label={w("admin.name")}
                placeholder={w("admin.name")}
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />{" "}
              <input
                aria-label={w("admin.language")}
                placeholder={w("admin.language")}
                value={form.language}
                onChange={(e) => setForm({ ...form, language: e.target.value })}
              />{" "}
              <select
                aria-label={w("admin.category")}
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value })}
              >
                <option value="UTILITY">UTILITY</option>
                <option value="MARKETING">MARKETING</option>
              </select>
              <br />
              <textarea
                aria-label={w("admin.body")}
                placeholder={w("admin.body")}
                value={form.body}
                rows={3}
                onChange={(e) => setForm({ ...form, body: e.target.value })}
              />
              <br />
              <textarea
                aria-label={w("admin.examples")}
                placeholder={w("admin.examples")}
                value={form.examples}
                rows={2}
                onChange={(e) => setForm({ ...form, examples: e.target.value })}
              />
              <br />
              <Button type="submit" variant="primary" disabled={busy || !form.name || !form.body}>
                {w("admin.submit")}
              </Button>
            </form>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
