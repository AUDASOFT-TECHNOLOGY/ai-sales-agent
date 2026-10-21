import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AdminAuthProvider } from "./AdminAuthContext";
import { AdminLoginPage } from "./AdminLoginPage";
import { AdminRoute } from "./AdminRoute";
import { AdminShell } from "./AdminShell";
import { AdminDashboardPage } from "./pages/AdminDashboardPage";

/** A fetch stub that answers `/admin/v1/*` the way the real FastAPI router does (see
 *  src/sales_app/api/admin.py and tests/app/test_admin_api.py), without a network or a backend. */
function stubFetch(opts: {
  me?: "authenticated" | "anonymous";
  login?: "ok" | 401 | 429;
}) {
  const me = opts.me ?? "anonymous";
  const login = opts.login ?? "ok";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/auth/me")) {
        return me === "authenticated"
          ? jsonResponse(200, { username: "alice", role: "editor", client_ids: ["alpha"] })
          : jsonResponse(401, { detail: "not authenticated" });
      }
      if (url.endsWith("/auth/login")) {
        const body = JSON.parse(String(init?.body));
        if (login === 401) return jsonResponse(401, { detail: "invalid username or password" });
        if (login === 429) return jsonResponse(429, { detail: "too many attempts" });
        return jsonResponse(200, { username: body.username, role: "editor", client_ids: ["alpha"] });
      }
      if (url.endsWith("/auth/logout")) {
        return jsonResponse(200, { status: "ok" });
      }
      // The real Dashboard (rendered once login succeeds) aggregates these three endpoints; these
      // tests exercise login/logout/routing, not the dashboard's own content, so an empty-but-valid
      // deployment is enough to let it render without error.
      if (url.endsWith("/clients")) {
        return jsonResponse(200, []);
      }
      if (url.endsWith("/agents")) {
        return jsonResponse(200, []);
      }
      if (url.endsWith("/settings/llm")) {
        return jsonResponse(200, { read_only: true, restricted: false, providers: [] });
      }
      throw new Error(`unexpected fetch ${url}`);
    }),
  );
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Mirrors the `/admin/*` route tree in App.tsx so these tests exercise the same nesting
 *  (AdminRoute guarding AdminShell, which lays out the nested section pages). */
/** Stand-in for the section pages these tests do not exercise (they have their own test files). */
function SectionStub({ title }: { title: string }) {
  return <h2>{title}</h2>;
}

function renderApp(initialPath = "/admin/login") {
  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AdminAuthProvider>
        <Routes>
          <Route path="/admin/login" element={<AdminLoginPage />} />
          <Route
            path="/admin"
            element={
              <AdminRoute>
                <AdminShell />
              </AdminRoute>
            }
          >
            <Route index element={<AdminDashboardPage />} />
            <Route path="clients" element={<SectionStub title="Clients" />} />
            <Route path="settings" element={<SectionStub title="Settings" />} />
          </Route>
        </Routes>
      </AdminAuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AdminLoginPage", () => {
  it("shows the sign-in form when nobody is logged in", async () => {
    stubFetch({ me: "anonymous" });
    renderApp();
    expect(await screen.findByLabelText("Username")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });

  it("redirects to /admin on a valid login", async () => {
    stubFetch({ me: "anonymous", login: "ok" });
    renderApp();
    const user = userEvent.setup();
    await screen.findByLabelText("Username");
    await user.type(screen.getByLabelText("Username"), "alice");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery-staple");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByText("alice")).toBeInTheDocument();
  });

  it("shows an error and stays on the form for a wrong password", async () => {
    stubFetch({ me: "anonymous", login: 401 });
    renderApp();
    const user = userEvent.setup();
    await screen.findByLabelText("Username");
    await user.type(screen.getByLabelText("Username"), "alice");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect username or password.");
    expect(screen.getByLabelText("Username")).toBeInTheDocument(); // still on the login page
  });

  it("shows a specific message when rate-limited", async () => {
    stubFetch({ me: "anonymous", login: 429 });
    renderApp();
    const user = userEvent.setup();
    await screen.findByLabelText("Username");
    await user.type(screen.getByLabelText("Username"), "alice");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts");
  });

  it("skips the form and goes straight to /admin when already signed in", async () => {
    stubFetch({ me: "authenticated" });
    renderApp();
    expect(await screen.findByRole("heading", { level: 1, name: "Dashboard" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Username")).not.toBeInTheDocument();
  });
});

describe("AdminRoute", () => {
  it("redirects to /admin/login when there is no session", async () => {
    stubFetch({ me: "anonymous" });
    renderApp("/admin");
    expect(await screen.findByLabelText("Username")).toBeInTheDocument();
  });

  it("renders the protected dashboard when the session is valid", async () => {
    stubFetch({ me: "authenticated" });
    renderApp("/admin");
    expect(await screen.findByRole("heading", { level: 1, name: "Dashboard" })).toBeInTheDocument();
  });

  it("also guards nested section routes, not just /admin itself", async () => {
    stubFetch({ me: "anonymous" });
    renderApp("/admin/clients");
    expect(await screen.findByLabelText("Username")).toBeInTheDocument();
  });
});

describe("AdminShell navigation", () => {
  it("lists every section in the sidebar and links to its route", async () => {
    stubFetch({ me: "authenticated" });
    renderApp("/admin");
    await screen.findByRole("heading", { level: 1, name: "Dashboard" });
    const nav = screen.getByRole("navigation", { name: "Admin sections" });
    for (const label of ["Dashboard", "Clients", "Billing", "Settings"]) {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(within(nav).queryByRole("link", { name: "Admin Users" })).not.toBeInTheDocument();
  });

  it("navigates to a section from the sidebar", async () => {
    stubFetch({ me: "authenticated" });
    renderApp("/admin");
    await screen.findByRole("heading", { level: 1, name: "Dashboard" });
    const user = userEvent.setup();
    await user.click(screen.getByRole("link", { name: "Clients" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Clients" })).toBeInTheDocument();
  });

  it("shows the real KPIs, and a dash (never a fake number) for metrics without a source", async () => {
    stubFetch({ me: "authenticated" });
    renderApp("/admin");
    await screen.findByRole("heading", { level: 1, name: "Dashboard" });
    // "Activity" only exists inside the dashboard body, unlike "Clients" which also labels the
    // sidebar nav -- wait on it first so the stat grid has actually loaded.
    expect(await screen.findByText("Activity")).toBeInTheDocument();
    const statGrid = document.querySelector(".admin-stat-grid") as HTMLElement;
    for (const label of ["Clients", "LLM Models configured", "Active Agents", "Total Revenue"]) {
      expect(within(statGrid).getByText(label)).toBeInTheDocument();
    }
    for (const label of ["Prospects contacted", "Conversations", "Conversions declared", "Conversions confirmed", "Revenue", "LLM cost", "WhatsApp cost"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getAllByText("—")).toHaveLength(8);
  });
});

describe("Logout", () => {
  it("returns to /admin/login and ends the session", async () => {
    stubFetch({ me: "authenticated" });
    renderApp("/admin");
    const user = userEvent.setup();
    await screen.findByRole("heading", { level: 1, name: "Dashboard" });
    await user.click(screen.getByRole("button", { name: "Log out" }));
    expect(await screen.findByLabelText("Username")).toBeInTheDocument();
  });
});

describe("waitFor sanity", () => {
  it("does not leave the loading state forever", async () => {
    stubFetch({ me: "anonymous" });
    renderApp("/admin");
    await waitFor(() => expect(screen.queryByText("Checking your session…")).not.toBeInTheDocument());
  });
});
