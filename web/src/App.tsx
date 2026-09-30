import { lazy } from "react";
import { HashRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { AdminAuthProvider } from "./admin/AdminAuthContext";
import { AdminLoginPage } from "./admin/AdminLoginPage";
import { AdminRoute } from "./admin/AdminRoute";
import { AdminShell } from "./admin/AdminShell";
import { AdminDashboardPage } from "./admin/pages/AdminDashboardPage";
import { AdminPlaceholderPage } from "./admin/pages/AdminPlaceholderPage";
import { I18nProvider } from "./i18n";
import { StoreProvider, useStore } from "./mock/store";
import { AppShell } from "./shell/AppShell";

// Screens are split per route so the shell stays under the initial JavaScript budget (doc C7).
const Dashboard = lazy(() => import("./screens/Dashboard").then((m) => ({ default: m.Dashboard })));
const Configure = lazy(() => import("./screens/configure/Configure").then((m) => ({ default: m.Configure })));
const Conversations = lazy(() => import("./screens/Conversations").then((m) => ({ default: m.Conversations })));
const History = lazy(() => import("./screens/History").then((m) => ({ default: m.History })));
const Notifications = lazy(() => import("./screens/Notifications").then((m) => ({ default: m.Notifications })));
const Prospects = lazy(() => import("./screens/Prospects").then((m) => ({ default: m.Prospects })));
const Runway = lazy(() => import("./screens/Runway").then((m) => ({ default: m.Runway })));

/** Home is the Dashboard. The Launch Runway is only the first-run onboarding. */
function Home() {
  const { state } = useStore();
  return <Navigate to={state.live ? "/dashboard" : "/setup"} replace />;
}

export function App() {
  return (
    <I18nProvider>
      <HashRouter>
        <Routes>
          {/* Admin: its own session (AdminAuthContext), never the client console's mock store. */}
          <Route
            element={
              <AdminAuthProvider>
                <Outlet />
              </AdminAuthProvider>
            }
          >
            <Route path="admin/login" element={<AdminLoginPage />} />
            <Route
              path="admin"
              element={
                <AdminRoute>
                  <AdminShell />
                </AdminRoute>
              }
            >
              <Route index element={<AdminDashboardPage />} />
              <Route path="clients" element={<AdminPlaceholderPage title="Clients" description="Manage client accounts, plans and access." />} />
              <Route path="agents" element={<AdminPlaceholderPage title="Agents" description="Configure and monitor sales agents across clients." />} />
              <Route path="billing" element={<AdminPlaceholderPage title="Billing" description="Subscriptions, invoices and payment settings." />} />
              <Route path="settings" element={<AdminPlaceholderPage title="Settings" description="Platform and LLM configuration." />} />
              <Route path="users" element={<AdminPlaceholderPage title="Admin Users" description="Manage admin accounts and roles." />} />
              <Route path="*" element={<Navigate to="/admin" replace />} />
            </Route>
          </Route>

          <Route
            element={
              <StoreProvider>
                <Outlet />
              </StoreProvider>
            }
          >
            <Route element={<AppShell />}>
              <Route index element={<Home />} />
              <Route path="setup" element={<Runway />} />
              <Route path="dashboard" element={<Dashboard />} />
              <Route path="prospects" element={<Prospects />} />
              <Route path="conversations" element={<Conversations />} />
              <Route path="conversations/:id" element={<Conversations />} />
              <Route path="configure" element={<Configure />} />
              <Route path="configure/:section" element={<Configure />} />
              <Route path="results" element={<Navigate to="/dashboard" replace />} />
              <Route path="history" element={<History />} />
              <Route path="notifications" element={<Notifications />} />
              <Route path="*" element={<Navigate to="/dashboard" replace />} />
            </Route>
          </Route>
        </Routes>
      </HashRouter>
    </I18nProvider>
  );
}
