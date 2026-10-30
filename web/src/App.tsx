import { Suspense, lazy } from "react";
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
import { AdminPaymentsPage } from "./admin/pages/AdminPaymentsPage";
import { AdminSettingsPage } from "./admin/pages/AdminSettingsPage";
import { AdminSitePage } from "./admin/pages/AdminSitePage";
import { ClientApprovalsPage } from "./client/ClientApprovalsPage";
import { ClientInvoicesPage } from "./client/billing/ClientInvoicesPage";
import { ClientAuthProvider } from "./client/ClientAuthContext";
import { ClientDashboardPage } from "./client/dashboard/ClientDashboardPage";
import { ClientNotificationsPage } from "./client/notifications/ClientNotificationsPage";
import { ClientHomePage } from "./client/ClientHomePage";
import { ClientLoginPage } from "./client/ClientLoginPage";
import { ClientProfilePage } from "./client/ClientProfilePage";
import { PlansPage } from "./client/PlansPage";
import { ClientRoute } from "./client/ClientRoute";
import { ClientTheme } from "./client/ClientTheme";
import { AccountLayout } from "./client/account/AccountLayout";
import { CircuitsPage } from "./client/circuits/CircuitsPage";
import { ConnectionsPage } from "./client/connections/ConnectionsPage";
import { ResultsLayout } from "./client/results/ResultsLayout";
import { SetupPage } from "./client/setup/SetupPage";
import { PreparePage } from "./client/prepare/PreparePage";
import { WorkflowPage } from "./client/workflow/WorkflowPage";
import { UsagePage } from "./client/usage/UsagePage";
import { SimulationPage } from "./client/prepare/SimulationPage";
import { TrainingPage } from "./client/training/TrainingPage";
import { RulesPage } from "./client/offer/RulesPage";
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
const LivePage = lazy(() => import("./client/live/LivePage").then((m) => ({ default: m.LivePage })));
const ProspectsRiver = lazy(() => import("./screens/ProspectsRiver").then((m) => ({ default: m.ProspectsRiver })));
// The public pages are split off too: a signed-in client never downloads the landing page.
const RootEntry = lazy(() => import("./landing/LandingPage").then((m) => ({ default: m.RootEntry })));
const PublicPage = lazy(() => import("./landing/PublicPage").then((m) => ({ default: m.PublicPage })));
const SignupPage = lazy(() => import("./landing/SignupPage").then((m) => ({ default: m.SignupPage })));
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
              <Route path="payments" element={<AdminPaymentsPage />} />
              <Route path="site" element={<AdminSitePage />} />
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
            {/* Public: the landing page (signed-in clients are sent to /home) and the free sign-up. */}
            <Route
              index
              element={
                <Suspense fallback={null}>
                  <RootEntry />
                </Suspense>
              }
            />
            <Route
              path="page/:key"
              element={
                <Suspense fallback={null}>
                  <PublicPage />
                </Suspense>
              }
            />
            <Route
              path="signup"
              element={
                <Suspense fallback={null}>
                  <SignupPage />
                </Suspense>
              }
            />
            <Route
              element={
                <ClientRoute>
                  <Outlet />
                </ClientRoute>
              }
            >
              <Route element={<AppShell />}>
                <Route path="home" element={<ClientHomePage />} />
                <Route path="plans" element={<PlansPage />} />
                <Route path="setup" element={<SetupPage />} />
                <Route path="prepare" element={<PreparePage />} />
                <Route path="workflow" element={<WorkflowPage />} />
                <Route path="usage" element={<UsagePage />} />
                <Route path="simulation" element={<SimulationPage />} />
                <Route path="training" element={<TrainingPage />} />

                <Route path="offer" element={<OfferLayout />}>
                  <Route index element={<Navigate to="products" replace />} />
                  <Route path="products" element={<ProductsPage />} />
                  <Route path="products/new" element={<ProductEditor />} />
                  <Route path="products/:id" element={<ProductEditor />} />
                  <Route path="payments" element={<PaymentsPage />} />
                  <Route path="rules" element={<RulesPage />} />
                  <Route path="business" element={<Navigate to="/offer/rules" replace />} />
                </Route>

                <Route path="connections" element={<ConnectionsPage />} />
                <Route path="circuits" element={<CircuitsPage />} />

                <Route path="conversations" element={<Conversations />} />
                <Route path="conversations/:id" element={<Conversations />} />
                <Route path="crm" element={<ProspectsRiver />} />
                <Route path="live" element={<LivePage />} />
                <Route path="prospects" element={<Prospects />} />

                <Route path="review" element={<ReviewLayout />}>
                  <Route index element={<ClientApprovalsPage embedded />} />
                  <Route path="payments" element={<ReviewPaymentsPage />} />
                </Route>

                <Route path="results" element={<ResultsLayout />}>
                  <Route index element={<ClientDashboardPage />} />
                </Route>

                <Route path="account" element={<AccountLayout />}>
                  <Route index element={<ClientInvoicesPage />} />
                </Route>
                <Route path="profile" element={<ClientProfilePage />} />
                <Route path="notifications" element={<ClientNotificationsPage />} />

                {/* Addresses of the previous menus keep working. */}
                <Route path="connections/*" element={<Navigate to="/connections" replace />} />
                <Route path="channel" element={<Navigate to="/connections" replace />} />
                <Route path="lanes" element={<Navigate to="/circuits" replace />} />
                <Route path="reports" element={<Navigate to="/notifications" replace />} />
                <Route path="reporting" element={<Navigate to="/notifications" replace />} />
                <Route path="results/daily" element={<Navigate to="/notifications" replace />} />
                <Route path="dashboard" element={<Navigate to="/results" replace />} />
                <Route path="billing" element={<Navigate to="/account" replace />} />
                <Route path="account/profile" element={<Navigate to="/profile" replace />} />
                <Route path="agent" element={<Navigate to="/home" replace />} />
                <Route path="history" element={<Navigate to="/home" replace />} />
                <Route path="approvals" element={<Navigate to="/review" replace />} />
                <Route path="settings" element={<Navigate to="/offer/rules" replace />} />
                <Route path="configure/*" element={<Navigate to="/offer/rules" replace />} />
                <Route path="*" element={<Navigate to="/home" replace />} />
              </Route>
            </Route>
          </Route>
        </Routes>
      </HashRouter>
    </I18nProvider>
  );
}
