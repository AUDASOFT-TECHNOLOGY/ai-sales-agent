import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AdminAuthProvider } from "./AdminAuthContext";
import { AdminLoginPage } from "./AdminLoginPage";
import { AdminRoute } from "./AdminRoute";
import { AdminHomePage } from "./AdminHomePage";

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
                <AdminHomePage />
              </AdminRoute>
            }
          />
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
    expect(await screen.findByText(/Signed in as/)).toBeInTheDocument();
    expect(screen.getByText("alice", { exact: false })).toBeInTheDocument();
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
    expect(await screen.findByText(/Signed in as/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Username")).not.toBeInTheDocument();
  });
});

describe("AdminRoute", () => {
  it("redirects to /admin/login when there is no session", async () => {
    stubFetch({ me: "anonymous" });
    renderApp("/admin");
    expect(await screen.findByLabelText("Username")).toBeInTheDocument();
  });

  it("renders the protected page when the session is valid", async () => {
    stubFetch({ me: "authenticated" });
    renderApp("/admin");
    expect(await screen.findByText(/Signed in as/)).toBeInTheDocument();
  });
});

describe("Logout", () => {
  it("returns to /admin/login and ends the session", async () => {
    stubFetch({ me: "authenticated" });
    renderApp("/admin");
    const user = userEvent.setup();
    await screen.findByText(/Signed in as/);
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
