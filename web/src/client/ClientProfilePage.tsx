/** Profile: the client edits its own name, login and WhatsApp number, and its password. The status
 *  is read-only (only Revansa activates or deactivates an account). A new WhatsApp number is not
 *  connected until a fresh onboarding: the page says so. No Meta credential or other technical
 *  secret exists in the data this page receives, so none can be shown. */

import { useState, type FormEvent } from "react";
import { useI18n, type MessageKey } from "../i18n";
import { Glass } from "../ui/Glass";
import { Button } from "../ui/Button";
import { MessageCircleWarning } from "lucide-react";
import { LockedField, TextInput } from "../ui/Controls";
import { ClientApiError, type ProfileChanges } from "./api";
import { useClientAuth } from "./ClientAuthContext";

const MIN_PASSWORD_LENGTH = 8; // same rule as the server (and as account creation)

function changeErrorKey(err: unknown): MessageKey {
  if (err instanceof ClientApiError) {
    if (err.status === 400) return "profile.password.error.current";
    if (err.status === 429) return "auth.error.tooMany";
    if (err.status === 422) return "profile.password.error.short";
  }
  return "auth.error.server";
}

/** Which field a server refusal is about, read from its message (the same text for any client). */
function saveErrorKey(err: unknown): MessageKey {
  if (err instanceof ClientApiError) {
    const detail = err.message.toLowerCase();
    if (err.status === 409) return detail.includes("login") ? "profile.error.loginTaken" : "profile.error.whatsappTaken";
    if (err.status === 400) {
      if (detail.includes("whatsapp")) return "profile.error.whatsappInvalid";
      if (detail.includes("login")) return "profile.error.loginInvalid";
      return "profile.error.nameInvalid";
    }
  }
  return "auth.error.server";
}

export function ClientProfilePage() {
  const { profile, changePassword, updateProfile } = useClientAuth();
  const { t } = useI18n();
  const [name, setName] = useState(profile?.name ?? "");
  const [loginName, setLoginName] = useState(profile?.login ?? "");
  const [whatsapp, setWhatsapp] = useState(profile?.whatsapp_number ?? "");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<MessageKey | null>(null);
  const [saved, setSaved] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<MessageKey | null>(null);
  const [done, setDone] = useState(false);

  if (!profile) return null; // unreachable behind ClientRoute; keeps the types honest

  const tooShort = next.length > 0 && next.length < MIN_PASSWORD_LENGTH;
  const mismatch = confirm.length > 0 && confirm !== next;

  const changes: ProfileChanges = {};
  if (name.trim() !== profile.name) changes.name = name.trim();
  if (loginName.trim() !== profile.login) changes.login = loginName.trim();
  if (whatsapp.trim() !== (profile.whatsapp_number ?? "")) changes.whatsapp_number = whatsapp.trim();
  const dirty = Object.keys(changes).length > 0;

  async function onSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaved(false);
    setSaving(true);
    setSaveError(null);
    try {
      await updateProfile(changes);
      setSaved(true);
    } catch (err) {
      setSaveError(saveErrorKey(err));
    } finally {
      setSaving(false);
    }
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setDone(false);
    if (next.length < MIN_PASSWORD_LENGTH) return setError("profile.password.error.short");
    if (next !== confirm) return setError("profile.password.error.mismatch");
    setSubmitting(true);
    setError(null);
    try {
      await changePassword(current, next, confirm);
      setCurrent("");
      setNext("");
      setConfirm("");
      setDone(true);
    } catch (err) {
      setError(changeErrorKey(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="rv-page">
      <div className="page-head">
        <h2 className="t-h1">{t("profile.title")}</h2>
      </div>

      <div className="rv-grid-2" style={{ alignItems: "start" }}>
        <Glass level={1} as="form" className="client-card" onSubmit={onSave} aria-label={t("profile.account")}>
          <h3 className="t-h2">{t("profile.account")}</h3>
          <div className="grid-2">
            <TextInput label={t("profile.name")} name="name" value={name} onChange={(e) => setName(e.target.value)} disabled={saving} maxLength={80} required />
            <TextInput label={t("profile.login")} name="login" autoComplete="username" value={loginName} onChange={(e) => setLoginName(e.target.value)} disabled={saving} maxLength={64} required />
            <TextInput
              label={t("profile.whatsapp")}
              name="whatsapp_number"
              type="tel"
              autoComplete="tel"
              value={whatsapp}
              onChange={(e) => setWhatsapp(e.target.value)}
              disabled={saving}
              maxLength={40}
              required
            />
            <LockedField label={t("profile.status")} value={profile.status === "active" ? t("profile.status.active") : t("profile.status.inactive")} helper={t("profile.status.helper")} />
          </div>
          {profile.whatsapp_reconnect_required ? (
            <p className="admin-alert admin-alert--info" role="status">
              <MessageCircleWarning size={16} aria-hidden /> {t("profile.whatsapp.reconnect")}
            </p>
          ) : null}
          {saveError ? (
            <p className="admin-alert admin-alert--danger" role="alert">
              {t(saveError)}
            </p>
          ) : null}
          {saved ? (
            <p className="admin-alert admin-alert--success" role="status">
              {t("profile.saved")}
            </p>
          ) : null}
          <div>
            <Button type="submit" variant="primary" loading={saving} disabled={!dirty}>
              {t("profile.save")}
            </Button>
          </div>
        </Glass>

        <Glass level={1} as="form" className="client-card" onSubmit={onSubmit} aria-label={t("profile.password.title")}>
          <h3 className="t-h2">{t("profile.password.title")}</h3>
          {profile.must_change_password ? (
            <p className="admin-alert admin-alert--info" role="status">
              {t("profile.password.temporary")}
            </p>
          ) : null}
          <TextInput
            label={t("profile.password.current")}
            name="current_password"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            disabled={submitting}
            required
          />
          <TextInput
            label={t("profile.password.new")}
            name="new_password"
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            disabled={submitting}
            helper={t("profile.password.rule", { n: MIN_PASSWORD_LENGTH })}
            error={tooShort ? t("profile.password.error.short") : undefined}
            required
          />
          <TextInput
            label={t("profile.password.confirm")}
            name="confirm_password"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            disabled={submitting}
            error={mismatch ? t("profile.password.error.mismatch") : undefined}
            required
          />
          {error ? (
            <p className="admin-alert admin-alert--danger" role="alert">
              {t(error)}
            </p>
          ) : null}
          {done ? (
            <p className="admin-alert admin-alert--success" role="status">
              {t("profile.password.done")}
            </p>
          ) : null}
          <div>
            <Button type="submit" variant="primary" loading={submitting}>
              {t("profile.password.submit")}
            </Button>
          </div>
        </Glass>
      </div>
    </div>
  );
}
