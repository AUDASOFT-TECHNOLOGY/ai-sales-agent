import { lazy } from "react";
import { HashRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { AdminAuthProvider } from "./admin/AdminAuthContext";
import { AdminLoginPage } from "./admin/AdminLoginPage";
import { AdminRoute } from "./admin/AdminRoute";
import { AdminShell } from "./admin/AdminShell";
import { AdminAuditPage } from "./admin/pages/AdminAuditPage";
import { AdminBillingPage } from "./admin/pages/AdminBillingPage";
import { AdminClientDetailPage } from "./admin/pages/AdminClientDetailPage";
import { AdminClientsPage } from "./admin/pages/AdminClientsPage";
import { AdminDashboardPage } from "./admin/pages/AdminDashboardPage";
import { AdminSettingsPage } from "./admin/pages/AdminSettingsPage";
import { ClientApprovalsPage } from "./client/ClientApprovalsPage";
import { ClientInvoicesPage } from "./client/billing/ClientInvoicesPage";
import { ClientAuthProvider } from "./client/ClientAuthContext";
import { ClientHomePage } from "./client/ClientHomePage";
import { ClientLoginPage } from "./client/ClientLoginPage";
import { ClientProfilePage } from "./client/ClientProfilePage";
import { ClientReportingPage } from "./client/ClientReportingPage";
import { ClientRoute } from "./client/ClientRoute";
import { ClientTheme } from "./client/ClientTheme";
import { ChannelsPage } from "./client/connections/ChannelsPage";
import { ConnectionsLayout } from "./client/connections/ConnectionsLayout";
import { SourcesPage } from "./client/connections/SourcesPage";
import { BusinessPage } from "./client/offer/BusinessPage";
import { OfferLayout } from "./client/offer/OfferLayout";
import { PaymentsPage } from "./client/offer/PaymentsPage";
import { ProductEditor } from "./client/offer/ProductEditor";
import { ProductsPage } from "./client/offer/ProductsPage";
import { ReviewLayout } from "./client/review/ReviewLayout";
import { ReviewPaymentsPage } from "./client/review/ReviewPaymentsPage";
import { I18nProvider } from "./i18n";
import { AppShell } from "./shell/AppShell";

// Screens that read the most data are split per route so the shell stays under the initial JavaScript budget.
const Conversations = lazy(() => import("./screens/Conversations").then((m) => ({ default: m.Conversations })));
const Prospects = lazy(() => import("./screens/Prospects").then((m) => ({ default: m.Prospects })));

export function App() {
  return (
    <I18nProvider>
      <HashRouter>
        <Routes>
          {/* Admin: its own session (AdminAuthContext), separate from the Client's. */}
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
              <Route path="audit" element={<AdminAuditPage />} />
              <Route path="settings" element={<AdminSettingsPage />} />
              <Route path="*" element={<Navigate to="/admin" replace />} />
            </Route>
          </Route>

          {/* Client: its own session and its own light theme. Everything but /login sits behind ClientRoute. */}
          <Route
            element={
              <ClientAuthProvider>
                <ClientTheme>
                  <Outlet />
                </ClientTheme>
              </ClientAuthProvider>
            }
          >
            <Route path="login" element={<ClientLoginPage />} />
            <Route
              element={
                <ClientRoute>
                  <Outlet />
                </ClientRoute>
              }
            >
              <Route element={<AppShell />}>
                <Route index element={<Navigate to="/home" replace />} />
                <Route path="home" element={<ClientHomePage />} />
                <Route path="prospects" element={<Prospects />} />
                <Route path="conversations" element={<Conversations />} />
                <Route path="conversations/:id" element={<Conversations />} />

                <Route path="review" element={<ReviewLayout />}>
                  <Route index element={<ClientApprovalsPage embedded />} />
                  <Route path="payments" element={<ReviewPaymentsPage />} />
                </Route>

                <Route path="offer" element={<OfferLayout />}>
                  <Route index element={<Navigate to="products" replace />} />
                  <Route path="products" element={<ProductsPage />} />
                  <Route path="products/new" element={<ProductEditor />} />
                  <Route path="products/:id" element={<ProductEditor />} />
                  <Route path="payments" element={<PaymentsPage />} />
                  <Route path="business" element={<BusinessPage />} />
                </Route>

                <Route path="connections" element={<ConnectionsLayout />}>
                  <Route index element={<Navigate to="sources" replace />} />
                  <Route path="sources" element={<SourcesPage />} />
                  <Route path="channels" element={<ChannelsPage />} />
                </Route>

                <Route path="reports" element={<ClientReportingPage />} />
                <Route path="billing" element={<ClientInvoicesPage />} />
                <Route path="profile" element={<ClientProfilePage />} />

                {/* Addresses of the previous menu keep working. */}
                <Route path="agent" element={<Navigate to="/home" replace />} />
                <Route path="setup" element={<Navigate to="/home" replace />} />
                <Route path="dashboard" element={<Navigate to="/home" replace />} />
                <Route path="results" element={<Navigate to="/home" replace />} />
                <Route path="history" element={<Navigate to="/home" replace />} />
                <Route path="notifications" element={<Navigate to="/home" replace />} />
                <Route path="approvals" element={<Navigate to="/review" replace />} />
                <Route path="settings" element={<Navigate to="/offer/business" replace />} />
                <Route path="configure/*" element={<Navigate to="/offer/business" replace />} />
                <Route path="channel" element={<Navigate to="/connections/channels" replace />} />
                <Route path="lanes" element={<Navigate to="/connections/sources" replace />} />
                <Route path="reporting" element={<Navigate to="/reports" replace />} />
                <Route path="*" element={<Navigate to="/home" replace />} />
              </Route>
            </Route>
          </Route>
        </Routes>
      </HashRouter>
    </I18nProvider>
  );
}
