/** Guards a route behind a valid Admin session: redirects to /admin/login while anonymous,
 *  shows a brief status while the initial session check is in flight. */

import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAdminAuth } from "./AdminAuthContext";

export function AdminRoute({ children }: { children: ReactNode }) {
  const { status } = useAdminAuth();

  if (status === "loading") {
    return (
      <div className="admin-page">
        <p className="c-2" role="status" aria-live="polite">
          Checking your session…
        </p>
      </div>
    );
  }
  if (status === "anonymous") {
    return <Navigate to="/admin/login" replace />;
  }
  return <>{children}</>;
}
