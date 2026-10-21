/** Guards the Client space behind a valid Client session: redirects to /login while anonymous,
 *  shows a brief status while the initial session check is in flight. */

import { Fragment, useState, type ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { DemoBanner } from "../demo/DemoBanner";
import { resetDemo } from "../demo/demoApi";
import { useIsDemo } from "../demo/demoMode";
import { useI18n } from "../i18n";
import { useClientAuth } from "./ClientAuthContext";

export function ClientRoute({ children }: { children: ReactNode }) {
  const { status } = useClientAuth();
  const { t } = useI18n();
  const demo = useIsDemo();
  const [epoch, setEpoch] = useState(0);

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
  if (demo) {
    // Reset: the data go back to their initial state and the screens are mounted again to read it.
    return (
      <>
        <DemoBanner onReset={() => { resetDemo(); setEpoch((n) => n + 1); }} />
        <Fragment key={epoch}>{children}</Fragment>
      </>
    );
  }
  return <>{children}</>;
}
