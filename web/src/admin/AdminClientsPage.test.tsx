import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AdminAuthProvider } from "./AdminAuthContext";
import { AdminRoute } from "./AdminRoute";
import { AdminShell } from "./AdminShell";
import { AdminClientDetailPage } from "./pages/AdminClientDetailPage";
import { AdminClientsPage } from "./pages/AdminClientsPage";

const ALPHA = {
  client_id: "alpha",
  display_name: "Alpha",
  status: "active",
  mode: "approval",
  timezone: "Africa/Tunis",
  daily_action_limit: 20,
  providers: { sheets: "google_sheets", llm: "openai", whatsapp: "whatsapp" },
};

const NAGA = {
  client_id: "naga",
  display_name: "Naga",
  status: "disabled",
  mode: "approval",
  timezone: "Africa/Tunis",
  daily_action_limit: 15,
  providers: { sheets: "google_sheets", llm: "openai", whatsapp: null },
};

const ALPHA_DETAIL = {
  ...ALPHA,
  max_attempts: 3,
  opt_out_keywords: ["stop"],
  sheets: {
    provider: "google_sheets",
    spreadsheet_id: "sheet-alpha",
    prospects_tab: "Prospects",
    actions_tab: "Actions",
    messages_tab: "Messages",
    log_tab: "Log",
    credentials_env: "GOOGLE_APPLICATION_CREDENTIALS",
  },
  llm: { provider: "openai", model: "test-model", api_key_env: "OPENAI_API_KEY" },
  whatsapp: {
    provider: "whatsapp",
    api_version: "v21.0",
    token_env: "ALPHA_TOKEN",
    phone_number_id_env: "ALPHA_PHONE_ID",
    app_secret_env: "ALPHA_APP_SECRET",
  },
  brain: { factory: "app_test_brains:good_brain", settings_version: 1 },
  schedule: { daily_at: "09:00", days: ["mon", "tue"], prepare_every_minutes: 60, dispatch_every_minutes: 5, recover_every_minutes: 15 },
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { username: "alice", role: "owner", client_ids: [] });
      }
      if (url.endsWith("/clients")) {
        return jsonResponse(200, [ALPHA, NAGA]);
      }
      if (url.endsWith("/clients/alpha")) {
        return jsonResponse(200, ALPHA_DETAIL);
      }
      if (url.endsWith("/clients/does-not-exist")) {
        return jsonResponse(404, { detail: "no such client" });
      }
      throw new Error(`unexpected fetch ${url}`);
    }),
  );
}

function renderApp(initialPath = "/admin/clients") {
  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AdminAuthProvider>
        <Routes>
          <Route
            path="/admin"
            element={
              <AdminRoute>
                <AdminShell />
              </AdminRoute>
            }
          >
            <Route path="clients" element={<AdminClientsPage />} />
            <Route path="clients/:clientId" element={<AdminClientDetailPage />} />
          </Route>
        </Routes>
      </AdminAuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AdminClientsPage", () => {
  it("lists clients from the API, including a disabled one", async () => {
    stubFetch();
    renderApp();
    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Naga")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Disabled")).toBeInTheDocument();
  });

  it("filters the list by search text", async () => {
    stubFetch();
    renderApp();
    await screen.findByText("Alpha");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Search clients"), "naga");
    expect(screen.queryByText("Alpha")).not.toBeInTheDocument();
    expect(screen.getByText("Naga")).toBeInTheDocument();
  });

  it("navigates to the client detail page on click", async () => {
    stubFetch();
    renderApp();
    await screen.findByText("Alpha");
    const user = userEvent.setup();
    await user.click(screen.getByText("Alpha"));
    expect(await screen.findByRole("heading", { level: 2, name: "Alpha" })).toBeInTheDocument();
  });
});

describe("AdminClientDetailPage", () => {
  it("shows configuration fields but never a secret value", async () => {
    stubFetch();
    renderApp("/admin/clients/alpha");
    await screen.findByRole("heading", { level: 2, name: "Alpha" });
    expect(screen.getByText("sheet-alpha")).toBeInTheDocument();
    expect(screen.getByText("GOOGLE_APPLICATION_CREDENTIALS")).toBeInTheDocument();
    expect(screen.getByText("OPENAI_API_KEY")).toBeInTheDocument();
    expect(screen.getByText("ALPHA_TOKEN")).toBeInTheDocument();
    // No secret value ever reaches the client-side response, so none can be on the page either.
    expect(document.body.textContent).not.toMatch(/sk-test|token-alpha|secret-alpha|phone-alpha/);
  });

  it("shows a not-found message for an unknown or out-of-scope client", async () => {
    stubFetch();
    renderApp("/admin/clients/does-not-exist");
    expect(await screen.findByText(/Client not found/)).toBeInTheDocument();
  });

  it("links back to the client list", async () => {
    stubFetch();
    renderApp("/admin/clients/alpha");
    await screen.findByRole("heading", { level: 2, name: "Alpha" });
    const user = userEvent.setup();
    await user.click(screen.getByRole("link", { name: /Back to Clients/ }));
    expect(await screen.findByText("Naga")).toBeInTheDocument();
  });
});

describe("waitFor sanity", () => {
  it("does not stay stuck on the loading state", async () => {
    stubFetch();
    renderApp();
    await waitFor(() => expect(screen.queryByText("Loading clients…")).not.toBeInTheDocument());
  });
});
