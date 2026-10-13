import { lazy } from "react";
import { HashRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { AdminAuthProvider } from "./admin/AdminAuthContext";
import { AdminLoginPage } from "./admin/AdminLoginPage";
import { AdminRoute } from "./admin/AdminRoute";
import { AdminShell } from "./admin/AdminShell";
import { AdminBillingPage } from "./admin/pages/AdminBillingPage";
import { AdminClientDetailPage } from "./admin/pages/AdminClientDetailPage";
import { AdminClientsPage } from "./admin/pages/AdminClientsPage";
import { AdminDashboardPage } from "./admin/pages/AdminDashboardPage";
import { AdminSettingsPage } from "./admin/pages/AdminSettingsPage";
import { ClientApprovalsPage } from "./client/ClientApprovalsPage";
import { ClientAuthProvider } from "./client/ClientAuthContext";
import { ClientChannelPage } from "./client/ClientChannelPage";
import { ClientLoginPage } from "./client/ClientLoginPage";
import { ClientProfilePage } from "./client/ClientProfilePage";
import { ClientRoute } from "./client/ClientRoute";
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
              <Route path="clients" element={<AdminClientsPage />} />
              <Route path="clients/:clientId" element={<AdminClientDetailPage />} />
              <Route path="billing" element={<AdminBillingPage />} />
              <Route path="settings" element={<AdminSettingsPage />} />
              <Route path="*" element={<Navigate to="/admin" replace />} />
            </Route>
          </Route>

          {/* Client: its own session (ClientAuthContext), separate from the Admin's. Everything but
              /login sits behind ClientRoute. */}
          <Route
            element={
              <ClientAuthProvider>
                <Outlet />
              </ClientAuthProvider>
            }
          >
            <Route path="login" element={<ClientLoginPage />} />
            <Route
              element={
                <ClientRoute>
                  <StoreProvider>
                    <Outlet />
                  </StoreProvider>
                </ClientRoute>
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
              <Route path="profile" element={<ClientProfilePage />} />
              <Route path="approvals" element={<ClientApprovalsPage />} />
              <Route path="channel" element={<ClientChannelPage />} />
              <Route path="*" element={<Navigate to="/dashboard" replace />} />
            </Route>
            </Route>
          </Route>
        </Routes>
      </HashRouter>
    </I18nProvider>
  );
}
