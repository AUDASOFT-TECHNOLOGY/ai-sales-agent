import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AdminAuthProvider } from "./AdminAuthContext";
import { AdminRoute } from "./AdminRoute";
import { AdminShell } from "./AdminShell";
import { AdminClientDetailPage } from "./pages/AdminClientDetailPage";

const ALPHA_DETAIL = {
  client_id: "alpha",
  display_name: "Alpha",
  status: "active",
  mode: "approval",
  timezone: "Africa/Tunis",
  daily_action_limit: 20,
  max_attempts: 3,
  opt_out_keywords: ["stop"],
  providers: { sheets: "google_sheets", llm: "openai", whatsapp: "whatsapp" },
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

const RESTRICTED_LLM_SETTINGS = {
  read_only: true,
  restricted: true,
  providers: [
    { provider: "openai", wired: true, allowed_models: ["test-model", "gpt-5"] },
    { provider: "anthropic", wired: false, allowed_models: ["claude"] },
  ],
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function stubFetch({
  role = "owner",
  llmSettings = RESTRICTED_LLM_SETTINGS,
  onPut,
}: {
  role?: string;
  llmSettings?: unknown;
  onPut?: (body: unknown) => Response;
} = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { username: "alice", role, client_ids: ["alpha"] });
      }
      if (url.endsWith("/settings/llm")) {
        return jsonResponse(200, llmSettings);
      }
      if (url.endsWith("/clients/alpha/llm") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        return onPut ? onPut(body) : jsonResponse(200, { ...body, api_key_env: "OPENAI_API_KEY" });
      }
      if (url.endsWith("/clients/alpha")) {
        return jsonResponse(200, ALPHA_DETAIL);
      }
      throw new Error(`unexpected fetch ${url} ${init?.method ?? "GET"}`);
    }),
  );
}

function renderApp(initialPath = "/admin/clients/alpha") {
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

describe("AdminClientDetailPage LLM configuration", () => {
  it("shows the current provider and model", async () => {
    stubFetch();
    renderApp();
    await screen.findByRole("heading", { level: 2, name: "Alpha" });
    await waitFor(() => expect(screen.getByLabelText("Provider")).toHaveValue("openai"));
    expect(screen.getByLabelText("Model")).toHaveValue("test-model");
  });

  it("only offers providers that are actually wired in this deployment", async () => {
    stubFetch();
    renderApp();
    await waitFor(() => expect(screen.getByLabelText("Provider")).toHaveValue("openai"));
    const options = Array.from(screen.getByLabelText("Provider").querySelectorAll("option")).map((o) => o.textContent);
    expect(options).toEqual(["openai"]); // "anthropic" is allow-listed but not wired: left out
  });

  it("lets an authorized admin change the model and save it", async () => {
    stubFetch();
    renderApp();
    await waitFor(() => expect(screen.getByLabelText("Model")).toHaveValue("test-model"));
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Model"), "gpt-5");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(screen.getByLabelText("Model")).toHaveValue("gpt-5");
  });

  it("shows a clear error when the server rejects the update", async () => {
    stubFetch({
      onPut: () => jsonResponse(400, { detail: "llm.model 'nope' is not allowed for provider 'openai'" }),
    });
    renderApp();
    await waitFor(() => expect(screen.getByLabelText("Model")).toHaveValue("test-model"));
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Model"), "gpt-5");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/is not allowed for provider/)).toBeInTheDocument();
  });

  it("disables saving for a viewer and explains why", async () => {
    stubFetch({ role: "viewer" });
    renderApp();
    await waitFor(() => expect(screen.getByLabelText("Provider")).toBeDisabled());
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("never asks for or displays an API key value", async () => {
    stubFetch();
    renderApp();
    await waitFor(() => expect(screen.getByLabelText("Model")).toHaveValue("test-model"));
    expect(screen.queryByLabelText(/api.?key/i)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/sk-test/);
  });
});
