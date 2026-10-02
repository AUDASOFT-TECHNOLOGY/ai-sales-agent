/** Guards the Client space behind a valid Client session: redirects to /login while anonymous,
 *  shows a brief status while the initial session check is in flight. */

import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useI18n } from "../i18n";
import { useClientAuth } from "./ClientAuthContext";

export function ClientRoute({ children }: { children: ReactNode }) {
  const { status } = useClientAuth();
  const { t } = useI18n();

  if (status === "loading") {
    return (
      <div className="admin-page">
        <p className="c-2" role="status" aria-live="polite">
          {t("auth.checking")}
        </p>
      </div>
    );
  }
  if (status === "anonymous") {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}
