/** Free sign-up. The account is created on the server (hashed password, unique email, 50 free credits,
 *  signed in); a plan chosen on the landing page is carried to the plans page, never silently bought. */

import { useCallback, useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { useI18n, type MessageKey } from "../i18n";
import { ClientApiError } from "../client/api";
import { useClientAuth } from "../client/ClientAuthContext";
import { publicPlans } from "../client/accessApi";
import { useFetched } from "../client/useFetched";
import { BrandMark } from "../ui/Brand";
import { Button } from "../ui/Button";
import { TextInput } from "../ui/Controls";
import { Glass } from "../ui/Glass";
import "./landing.css";

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function errorKey(err: unknown): MessageKey {
  if (err instanceof ClientApiError) {
    if (err.status === 409) return "lp.signup.error.exists";
    if (err.status === 429) return "lp.signup.error.tooMany";
    if (err.status === 400 || err.status === 422) return "lp.signup.error.email";
  }
  return "lp.signup.error.server";
}

export function SignupPage() {
  const { t } = useI18n();
  const { status, signup } = useClientAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const planId = params.get("plan");
  const load = useCallback(() => publicPlans(), []);
  const plans = useFetched(load);
  const chosen = planId ? plans.value?.find((p) => p.plan_id === planId) : undefined;

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [website, setWebsite] = useState(""); // honeypot: people never see it
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<MessageKey | null>(null);

  if (status === "authenticated" && !submitting) return <Navigate to={chosen ? `/plans?plan=${chosen.plan_id}` : "/home"} replace />;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim()) return setError("lp.signup.error.name");
    if (!EMAIL.test(email.trim())) return setError("lp.signup.error.email");
    if (password.length < 8) return setError("lp.signup.error.password");
    setSubmitting(true);
    setError(null);
    try {
      await signup(name.trim(), email.trim(), password, website);
      navigate(chosen ? `/plans?plan=${chosen.plan_id}` : "/home", { replace: true });
    } catch (err) {
      setError(errorKey(err));
      setPassword("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="admin-page">
      <Glass level={2} as="form" className="admin-card" onSubmit={onSubmit} noValidate aria-label={t("lp.signup.title")}>
        <div className="admin-card__head">
          <Link to="/" aria-label={t("brand.name")}>
            <BrandMark size={36} />
          </Link>
          <h1 className="t-h1">{t("lp.signup.title")}</h1>
          <p className="c-2 t-body-sm">{t("lp.signup.sub")}</p>
          {chosen ? <p className="c-2 t-body-sm">{t("lp.signup.plan", { name: chosen.name })}</p> : null}
        </div>
        <TextInput label={t("lp.signup.name")} name="name" autoComplete="organization" value={name} onChange={(e) => setName(e.target.value)} disabled={submitting} required autoFocus maxLength={80} />
        <TextInput label={t("lp.signup.email")} name="email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} disabled={submitting} required maxLength={64} />
        <TextInput
          label={t("lp.signup.password")}
          name="password"
          type="password"
          autoComplete="new-password"
          helper={t("lp.signup.password.hint")}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={submitting}
          required
          minLength={8}
        />
        <div className="lp-trap" aria-hidden>
          <label>
            Website
            <input name="website" tabIndex={-1} autoComplete="off" value={website} onChange={(e) => setWebsite(e.target.value)} />
          </label>
        </div>
        {error ? (
          <p className="admin-alert admin-alert--danger" role="alert">
            {t(error)}
          </p>
        ) : null}
        <Button type="submit" variant="primary" loading={submitting}>
          {t("lp.signup.submit")}
        </Button>
        <p className="c-3 t-body-sm">{t("lp.signup.rules")}</p>
        <p className="c-2 t-body-sm">
          {t("lp.signup.have")} <Link to="/login">{t("lp.signup.login")}</Link>
        </p>
      </Glass>
    </div>
  );
}
