/** Minimal landing page behind the Admin session: proves login -> session -> protected access
 *  works end to end. The real Admin (Configuration, CRM, Dashboard) is not built yet. */

import { Glass } from "../ui/Glass";
import { Button } from "../ui/Button";
import { useAdminAuth } from "./AdminAuthContext";

export function AdminHomePage() {
  const { admin, logout } = useAdminAuth();
  if (!admin) return null; // AdminRoute only renders this while authenticated

  return (
    <div className="admin-page">
      <Glass level={2} className="admin-card">
        <div>
          <h1 className="t-h1">Revansa Admin</h1>
          <p className="c-2">
            Signed in as <strong>{admin.username}</strong> &middot; role <strong>{admin.role}</strong>
          </p>
        </div>
        <p className="c-3 t-body-sm">
          {admin.role === "owner"
            ? "Access: every client."
            : admin.client_ids.length > 0
              ? `Clients: ${admin.client_ids.join(", ")}`
              : "No client assigned yet."}
        </p>
        <Button variant="secondary" onClick={() => void logout()}>
          Log out
        </Button>
      </Glass>
    </div>
  );
}
