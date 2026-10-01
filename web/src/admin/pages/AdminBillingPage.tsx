/** Billing: placeholder until the content of this screen is defined. */

import { Receipt } from "lucide-react";
import { Glass } from "../../ui/Glass";

export function AdminBillingPage() {
  return (
    <Glass level={1} className="admin-coming-soon">
      <Receipt size={32} aria-hidden />
      <h2 className="t-h2">Coming Soon</h2>
      <p className="c-3 t-body-sm">The Billing section is under construction.</p>
    </Glass>
  );
}
