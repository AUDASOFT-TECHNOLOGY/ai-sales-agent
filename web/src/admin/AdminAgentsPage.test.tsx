import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AdminAuthProvider } from "./AdminAuthContext";
import { AdminRoute } from "./AdminRoute";
import { AdminShell } from "./AdminShell";
import { AdminAgentDetailPage } from "./pages/AdminAgentDetailPage";
import { AdminAgentsPage } from "./pages/AdminAgentsPage";

const ALPHA = {
  agent_id: "alpha",
  client_id: "alpha",
  display_name: "Alpha",
  status: "active",
  mode: "approval",
  brain: { factory: "app_test_brains:good_brain", settings_version: 1 },
  llm: { provider: "openai", model: "test-model" },
};

const NAGA = {
  agent_id: "naga",
  client_id: "naga",
  display_name: "Naga",
  status: "disabled",
  mode: "approval",
  brain: { factory: null, settings_version: null },
  llm: { provider: "openai", model: null },
};

const ALPHA_DETAIL = {
  ...ALPHA,
  timezone: "Africa/Tunis",
  daily_action_limit: 20,
  max_attempts: 3,
  channels: { sheets: "google_sheets", whatsapp: "whatsapp" },
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
      if (url.endsWith("/agents")) {
        return jsonResponse(200, [ALPHA, NAGA]);
      }
      if (url.endsWith("/agents/alpha")) {
        return jsonResponse(200, ALPHA_DETAIL);
      }
      if (url.endsWith("/agents/does-not-exist")) {
        return jsonResponse(404, { detail: "no such agent" });
      }
      throw new Error(`unexpected fetch ${url}`);
    }),
  );
}

function renderApp(initialPath = "/admin/agents") {
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
            <Route path="agents" element={<AdminAgentsPage />} />
            <Route path="agents/:agentId" element={<AdminAgentDetailPage />} />
          </Route>
        </Routes>
      </AdminAuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AdminAgentsPage", () => {
  it("lists agents from the API, including a disabled one", async () => {
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
    await user.type(screen.getByLabelText("Search agents"), "naga");
    expect(screen.queryByText("Alpha")).not.toBeInTheDocument();
    expect(screen.getByText("Naga")).toBeInTheDocument();
  });

  it("navigates to the agent detail page on click", async () => {
    stubFetch();
    renderApp();
    await screen.findByText("Alpha");
    const user = userEvent.setup();
    await user.click(screen.getByText("Alpha"));
    expect(await screen.findByRole("heading", { level: 2, name: "Alpha" })).toBeInTheDocument();
  });
});

describe("AdminAgentDetailPage", () => {
  it("shows configuration fields but never a secret value or an env-var name", async () => {
    stubFetch();
    renderApp("/admin/agents/alpha");
    await screen.findByRole("heading", { level: 2, name: "Alpha" });
    expect(screen.getByText("app_test_brains:good_brain")).toBeInTheDocument();
    expect(screen.getByText("test-model")).toBeInTheDocument();
    expect(screen.getByText("google_sheets")).toBeInTheDocument();
    // Only provider names and the Brain factory/settings version are shown -- no *_env field
    // exists on this response at all, so none can render here either.
    expect(document.body.textContent).not.toMatch(/sk-test|token-alpha|secret-alpha|phone-alpha|_env/);
  });

  it("shows a not-found message for an unknown or out-of-scope agent", async () => {
    stubFetch();
    renderApp("/admin/agents/does-not-exist");
    expect(await screen.findByText(/Agent not found/)).toBeInTheDocument();
  });

  it("links back to the agent list", async () => {
    stubFetch();
    renderApp("/admin/agents/alpha");
    await screen.findByRole("heading", { level: 2, name: "Alpha" });
    const user = userEvent.setup();
    await user.click(screen.getByRole("link", { name: /Back to Agents/ }));
    expect(await screen.findByText("Naga")).toBeInTheDocument();
  });
});

describe("waitFor sanity", () => {
  it("does not stay stuck on the loading state", async () => {
    stubFetch();
    renderApp();
    await waitFor(() => expect(screen.queryByText("Loading agents…")).not.toBeInTheDocument());
  });
});
