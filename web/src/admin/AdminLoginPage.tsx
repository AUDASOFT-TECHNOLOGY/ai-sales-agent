import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { Glass } from "../ui/Glass";
import { TextInput } from "../ui/Controls";
import { Button } from "../ui/Button";
import { BrandMark } from "../ui/Brand";
import { AdminApiError } from "./api";
import { useAdminAuth } from "./AdminAuthContext";

export function AdminLoginPage() {
  const { status, login } = useAdminAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Already signed in (session cookie still valid from an earlier visit): skip the form.
  if (status === "authenticated") {
    return <Navigate to="/admin" replace />;
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(username, password);
      navigate("/admin", { replace: true });
    } catch (err) {
      setError(loginErrorMessage(err));
      setPassword("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="admin-page">
      <Glass level={2} as="form" className="admin-card" onSubmit={onSubmit} aria-label="Admin sign in">
        <div className="admin-card__head">
          <BrandMark size={36} />
          <h1 className="t-h1">Revansa Admin</h1>
          <p className="c-2 t-body-sm">Sign in with your operator account.</p>
        </div>
        <TextInput
          label="Username"
          name="username"
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          disabled={submitting}
          required
          autoFocus
        />
        <TextInput
          label="Password"
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
            {error}
          </p>
        ) : null}
        <Button type="submit" variant="primary" loading={submitting}>
          Sign in
        </Button>
      </Glass>
    </div>
  );
}

function loginErrorMessage(err: unknown): string {
  if (err instanceof AdminApiError) {
    if (err.status === 401) return "Incorrect username or password.";
    if (err.status === 429) return "Too many attempts. Try again in a few minutes.";
    if (err.status === 0) return "Could not reach the server. Check your connection and try again.";
    return err.message || "Sign-in failed.";
  }
  return "Sign-in failed.";
}
