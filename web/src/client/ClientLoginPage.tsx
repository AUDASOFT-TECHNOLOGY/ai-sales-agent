import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useI18n, type MessageKey } from "../i18n";
import { Glass } from "../ui/Glass";
import { TextInput } from "../ui/Controls";
import { Button } from "../ui/Button";
import { BrandMark } from "../ui/Brand";
import { ClientApiError } from "./api";
import { useClientAuth } from "./ClientAuthContext";

/** Wrong password and unknown login both come back as 401, on purpose: nothing here tells which. */
function loginErrorKey(err: unknown): MessageKey {
  if (err instanceof ClientApiError) {
    if (err.status === 401) return "auth.error.invalid";
    if (err.status === 403) return "auth.error.inactive";
    if (err.status === 429) return "auth.error.tooMany";
  }
  return "auth.error.server";
}

export function ClientLoginPage() {
  const { status, login: signIn } = useClientAuth();
  const { t } = useI18n();
  const navigate = useNavigate();
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<MessageKey | null>(null);

  // Already signed in (session cookie still valid from an earlier visit): skip the form.
  if (status === "authenticated") {
    return <Navigate to="/" replace />;
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await signIn(login, password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(loginErrorKey(err));
      setPassword("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="admin-page">
      <Glass level={2} as="form" className="admin-card" onSubmit={onSubmit} aria-label={t("auth.title")}>
        <div className="admin-card__head">
          <BrandMark size={36} />
          <h1 className="t-h1">{t("brand.name")}</h1>
          <p className="c-2 t-body-sm">{t("auth.subtitle")}</p>
        </div>
        <TextInput
          label={t("auth.login")}
          name="login"
          autoComplete="username"
          value={login}
          onChange={(e) => setLogin(e.target.value)}
          disabled={submitting}
          required
          autoFocus
        />
        <TextInput
          label={t("auth.password")}
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={submitting}
          required
        />
        {error ? (
          <p className="admin-alert admin-alert--danger" role="alert">
            {t(error)}
          </p>
        ) : null}
        <Button type="submit" variant="primary" loading={submitting}>
          {t("auth.submit")}
        </Button>
      </Glass>
    </div>
  );
}
