import { lazy } from "react";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
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
      <StoreProvider>
        <HashRouter>
          <Routes>
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
          </Routes>
        </HashRouter>
      </StoreProvider>
    </I18nProvider>
  );
}
