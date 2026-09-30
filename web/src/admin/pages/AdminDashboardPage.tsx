/** Admin landing page: placeholder stats only. No backend metrics are wired up yet. */

import { Users, Bot, CreditCard, MessageSquare } from "lucide-react";
import { Glass, type Tint } from "../../ui/Glass";

interface StatCard {
  label: string;
  icon: typeof Users;
  tint: Tint;
}

const STATS: StatCard[] = [
  { label: "Total Clients", icon: Users, tint: "action" },
  { label: "Active Agents", icon: Bot, tint: "agent" },
  { label: "Monthly Revenue", icon: CreditCard, tint: "success" },
  { label: "Conversations Today", icon: MessageSquare, tint: "attention" },
];

export function AdminDashboardPage() {
  return (
    <div className="admin-dashboard">
      <p className="c-2 t-body-sm admin-dashboard__notice" role="note">
        These figures are placeholders — no live data is connected yet.
      </p>
      <div className="admin-stat-grid">
        {STATS.map(({ label, icon: Icon, tint }) => (
          <Glass key={label} level={1} tint={tint} className="admin-stat-card">
            <div className="admin-stat-card__head">
              <Icon size={20} aria-hidden />
              <span className="admin-stat-card__badge">Placeholder</span>
            </div>
            <p className="admin-stat-card__value tabular" aria-hidden>
              —
            </p>
            <p className="c-2 t-body-sm">{label}</p>
          </Glass>
        ))}
      </div>
    </div>
  );
}
