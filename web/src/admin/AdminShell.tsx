/** Admin shell: sidebar navigation + protected content area. Rendered inside AdminRoute, so
 *  `admin` from AdminAuthContext is always set here. Isolated from the client prototype's
 *  AppShell (mock store, i18n nav) — this is Revansa's own operator console. */

import { Outlet, NavLink, useLocation } from "react-router-dom";
import { LayoutDashboard, Users, Bot, CreditCard, Settings, ShieldCheck, LogOut } from "lucide-react";
import { Glass } from "../ui/Glass";
import { IconButton } from "../ui/Button";
import { BrandMark } from "../ui/Brand";
import { cx } from "../ui/cx";
import { useAdminAuth } from "./AdminAuthContext";

const NAV: Array<{ to: string; label: string; icon: typeof LayoutDashboard; end?: boolean }> = [
  { to: "/admin", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/admin/clients", label: "Clients", icon: Users },
  { to: "/admin/agents", label: "Agents", icon: Bot },
  { to: "/admin/billing", label: "Billing", icon: CreditCard },
  { to: "/admin/settings", label: "Settings", icon: Settings },
  { to: "/admin/users", label: "Admin Users", icon: ShieldCheck },
];

function titleFor(pathname: string): string {
  // Longest-prefix match against NAV so a nested route (e.g. /admin/clients/:id) inherits its
  // section's title without a separate entry per sub-route.
  const match = [...NAV].sort((a, b) => b.to.length - a.to.length).find((item) => pathname === item.to || (item.to !== "/admin" && pathname.startsWith(`${item.to}/`)));
  return match?.label ?? "Dashboard";
}

function initials(username: string): string {
  return username.slice(0, 2).toUpperCase();
}

export function AdminShell() {
  const { admin, logout } = useAdminAuth();
  const { pathname } = useLocation();
  if (!admin) return null; // AdminRoute only renders this while authenticated

  const title = titleFor(pathname);

  return (
    <div className="admin-shell">
      <Glass as="aside" level={2} className="admin-shell__sidebar" aria-label="Admin navigation">
        <div className="admin-shell__brand">
          <BrandMark size={28} />
          <span className="admin-shell__brand-text">Revansa Admin</span>
        </div>
        <nav className="admin-shell__nav" aria-label="Admin sections">
          <ul>
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <li key={to}>
                <NavLink to={to} end={end} className={({ isActive }) => cx("admin-shell__nav-item", isActive && "is-active")}>
                  <Icon size={20} aria-hidden />
                  <span className="admin-shell__nav-label">{label}</span>
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <div className="admin-shell__footer">
          <div className="admin-shell__identity">
            <span className="avatar-sm" aria-hidden>
              {initials(admin.username)}
            </span>
            <span className="admin-shell__identity-text">
              <span className="t-label">{admin.username}</span>
              <span className="c-3 t-body-sm">{admin.role}</span>
            </span>
          </div>
          <IconButton label="Log out" onClick={() => void logout()}>
            <LogOut size={18} aria-hidden />
          </IconButton>
        </div>
      </Glass>
      <div className="admin-shell__main">
        <header className="admin-shell__topbar">
          <h1 className="t-h1">{title}</h1>
        </header>
        <main id="admin-main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
