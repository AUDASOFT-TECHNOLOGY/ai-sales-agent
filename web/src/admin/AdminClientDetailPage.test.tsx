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
  account: null,
  meta: { status: "not_connected", credentials_stored: false },
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

const ALPHA_WITH_ACCOUNT = {
  ...ALPHA_DETAIL,
  account: { login: "alpha.login", whatsapp_number: "+216 22 768 307", logo: null, must_change_password: true },
};

function stubFetch({
  role = "owner",
  llmSettings = RESTRICTED_LLM_SETTINGS,
  detail = ALPHA_DETAIL,
  onPut,
  onPatch,
  credentials = [],
  calls,
}: {
  role?: string;
  llmSettings?: unknown;
  detail?: unknown;
  onPut?: (body: unknown) => Response;
  onPatch?: (body: Record<string, unknown>) => Response;
  /** What the client's stored credentials report (configured or not; never a value). */
  credentials?: Array<{ key: string; configured: boolean }>;
  /** Every request made, as "METHOD path", to check which routes a page used. */
  calls?: string[];
} = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      calls?.push(`${init?.method ?? "GET"} ${url.replace(/^.*\/admin\/v1/, "")}`);
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { username: "alice", role, client_ids: ["alpha"] });
      }
      if (url.endsWith("/clients/alpha/integrations")) {
        return jsonResponse(
          200,
          credentials.map((c) => ({ ...c, label: c.key, scope: "client", sensitive: true, value: null, updated_at: null, updated_by: null })),
        );
      }
      if (url.endsWith("/clients/alpha/integrations/telegram.bot_token") && init?.method === "PUT") {
        return jsonResponse(200, { key: "telegram.bot_token", label: "Telegram bot token", scope: "client", sensitive: true, configured: true, value: null, updated_at: null, updated_by: null });
      }
      if (url.includes("/clients/alpha/integrations/telegram/")) {
        const action = url.split("/").pop();
        return jsonResponse(200, { ok: true, message: `telegram ${action} ok`, details: {} });
      }
      if (url.endsWith("/settings/llm")) {
        return jsonResponse(200, llmSettings);
      }
      if (url.endsWith("/clients/alpha/llm") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        return onPut ? onPut(body) : jsonResponse(200, { ...body, api_key_env: "OPENAI_API_KEY" });
      }
      if (url.endsWith("/clients/alpha") && init?.method === "PATCH") {
        return onPatch ? onPatch(JSON.parse(String(init.body))) : jsonResponse(200, detail);
      }
      if (url.endsWith("/clients/alpha")) {
        return jsonResponse(200, detail);
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

describe("AdminClientDetailPage LLM model", () => {
  it("shows the current model and no provider selector", async () => {
    stubFetch();
    renderApp();
    await screen.findByRole("heading", { level: 2, name: "Alpha" });
    await waitFor(() => expect(screen.getByLabelText("Modèle LLM")).toHaveValue("openai::test-model"));
    expect(screen.queryByLabelText("Provider")).not.toBeInTheDocument();
  });

  it("lets an authorized admin change the model and save it", async () => {
    stubFetch();
    renderApp();
    await waitFor(() => expect(screen.getByLabelText("Modèle LLM")).toHaveValue("openai::test-model"));
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Modèle LLM"), "gpt-5");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(screen.getByLabelText("Modèle LLM")).toHaveValue("openai::gpt-5");
  });

  it("shows a clear error when the server rejects the update", async () => {
    stubFetch({
      onPut: () => jsonResponse(400, { detail: "llm.model 'nope' is not allowed for provider 'openai'" }),
    });
    renderApp();
    await waitFor(() => expect(screen.getByLabelText("Modèle LLM")).toHaveValue("openai::test-model"));
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Modèle LLM"), "gpt-5");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/is not allowed for provider/)).toBeInTheDocument();
  });

  it("offers only the models enabled in Settings, from wired providers", async () => {
    stubFetch();
    renderApp();
    await waitFor(() => expect(screen.getByLabelText("Modèle LLM")).toHaveValue("openai::test-model"));
    const options = Array.from(screen.getByLabelText("Modèle LLM").querySelectorAll("option")).map((o) => o.textContent);
    expect(options).toEqual(["Select a model…", "test-model", "gpt-5"]); // "claude" is not wired
  });

  it("labels each option with the name Settings gives the model, not a hard-coded list", async () => {
    stubFetch({
      llmSettings: {
        read_only: true,
        restricted: false,
        providers: [
          {
            provider: "openai",
            wired: true,
            allowed_models: ["test-model", "model-x"],
            models: [
              { id: "test-model", name: "Luna" },
              { id: "model-x", name: "Sol" },
            ],
          },
        ],
      },
    });
    renderApp();
    await waitFor(() => expect(screen.getByLabelText("Modèle LLM")).toHaveValue("openai::test-model"));
    const options = Array.from(screen.getByLabelText("Modèle LLM").querySelectorAll("option")).map((o) => o.textContent);
    expect(options).toEqual(["Select a model…", "Luna", "Sol"]);
  });

  it("shows an empty dropdown when Settings enables no model", async () => {
    stubFetch({ llmSettings: { read_only: true, restricted: false, providers: [{ provider: "openai", wired: true, allowed_models: null }] } });
    renderApp();
    const select = await screen.findByLabelText("Modèle LLM");
    expect(select.tagName).toBe("SELECT");
    expect(select).toBeDisabled();
    expect(Array.from(select.querySelectorAll("option")).map((o) => o.textContent)).toEqual(["No model available"]);
    expect(screen.getByText(/No active LLM model is configured in Settings/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("disables saving for a viewer", async () => {
    stubFetch({ role: "viewer" });
    renderApp();
    await waitFor(() => expect(screen.getByLabelText("Modèle LLM")).toBeDisabled());
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it.each(["operator", "editor"])("lets only an owner assign a model: not a %s", async (role) => {
    // The server refuses PUT /clients/:id/llm to every role but an owner; the form says so up front.
    stubFetch({ role });
    renderApp();
    await waitFor(() => expect(screen.getByLabelText("Modèle LLM")).toBeDisabled());
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("lets an owner pick another model", async () => {
    stubFetch({ role: "owner" });
    renderApp();
    await waitFor(() => expect(screen.getByLabelText("Modèle LLM")).toBeEnabled());
  });

  it("never asks for or displays an API key value", async () => {
    stubFetch();
    renderApp();
    await waitFor(() => expect(screen.getByLabelText("Modèle LLM")).toHaveValue("openai::test-model"));
    expect(screen.queryByLabelText(/api.?key/i)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/sk-test/);
  });
});

describe("AdminClientDetailPage edit mode", () => {
  async function openEditForm() {
    const user = userEvent.setup();
    await screen.findByRole("heading", { level: 2, name: "Alpha" });
    await user.click(screen.getByRole("button", { name: "Modifier" }));
    await screen.findByRole("button", { name: "Enregistrer" });
    await waitFor(() => expect(screen.getByLabelText("Modèle LLM")).toHaveValue("openai::test-model"));
    return user;
  }

  it("shows a Modifier button that opens the form with the current values", async () => {
    stubFetch({ detail: ALPHA_WITH_ACCOUNT });
    renderApp();
    await openEditForm();
    expect(screen.getByLabelText("Name")).toHaveValue("Alpha");
    expect(screen.getByLabelText("Login")).toHaveValue("alpha.login");
    expect(screen.getByLabelText(/New password/)).toHaveValue("");
    expect(screen.getByLabelText("WhatsApp number")).toHaveValue("+216 22 768 307");
    expect(screen.getByLabelText("Modèle LLM")).toHaveValue("openai::test-model");
    expect(screen.getByLabelText("Daily quota")).toHaveValue(20);
    expect(screen.getByLabelText("Active")).toBeChecked();
    expect(screen.getByLabelText(/Logo/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Annuler" })).toBeInTheDocument();
  });

  it("lets the admin create the account of a client without a profile, with the password", async () => {
    let sent: Record<string, unknown> | null = null;
    stubFetch({
      onPatch: (body) => {
        sent = body;
        return jsonResponse(200, ALPHA_WITH_ACCOUNT);
      },
    });
    renderApp();
    const user = await openEditForm();
    expect(screen.getByLabelText("WhatsApp number")).toBeEnabled();
    await user.type(screen.getByLabelText("Login"), "alpha.login");
    expect(screen.getByRole("button", { name: "Enregistrer" })).toBeDisabled(); // needs all three
    await user.type(screen.getByLabelText(/New password/), "admin-set-pass");
    await user.type(screen.getByLabelText("WhatsApp number"), "+216 22 768 307");
    await user.click(screen.getByRole("button", { name: "Enregistrer" }));
    await screen.findByText("Client mis à jour.");
    expect(sent).toEqual({ login: "alpha.login", new_password: "admin-set-pass", whatsapp_number: "+216 22 768 307" });
  });

  it("never offers a Meta credential field or reveals the password", async () => {
    stubFetch({ detail: ALPHA_WITH_ACCOUNT });
    renderApp();
    await openEditForm();
    expect(screen.queryByLabelText(/access token|phone number id|app secret|verify token/i)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/New password/)).toHaveAttribute("type", "password");
  });

  it("is not offered to a non-owner", async () => {
    stubFetch({ role: "editor" });
    renderApp();
    await screen.findByRole("heading", { level: 2, name: "Alpha" });
    expect(screen.queryByRole("button", { name: "Modifier" })).not.toBeInTheDocument();
  });

  it("sends only the changed fields, then shows the new values and a success message", async () => {
    let sent: Record<string, unknown> | null = null;
    stubFetch({
      detail: ALPHA_WITH_ACCOUNT,
      onPatch: (body) => {
        sent = body;
        return jsonResponse(200, { ...ALPHA_WITH_ACCOUNT, display_name: "Alpha Two", daily_action_limit: 30, status: "disabled" });
      },
    });
    renderApp();
    const user = await openEditForm();
    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "Alpha Two");
    await user.clear(screen.getByLabelText("Daily quota"));
    await user.type(screen.getByLabelText("Daily quota"), "30");
    await user.type(screen.getByLabelText(/New password/), "brand-new-pass");
    await user.selectOptions(screen.getByLabelText("Modèle LLM"), "gpt-5");
    await user.click(screen.getByLabelText("Inactive"));
    await user.click(screen.getByRole("button", { name: "Enregistrer" }));

    expect(await screen.findByText("Client mis à jour.")).toBeInTheDocument();
    expect(sent).toEqual({
      display_name: "Alpha Two",
      daily_action_limit: 30,
      new_password: "brand-new-pass",
      llm_provider: "openai",
      llm_model: "gpt-5",
      status: "inactive",
    });
    expect(screen.getByRole("heading", { level: 2, name: "Alpha Two" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Enregistrer" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Modifier" })).toBeInTheDocument();
  });

  it("omits the password when the field is left empty", async () => {
    let sent: Record<string, unknown> | null = null;
    stubFetch({
      detail: ALPHA_WITH_ACCOUNT,
      onPatch: (body) => {
        sent = body;
        return jsonResponse(200, ALPHA_WITH_ACCOUNT);
      },
    });
    renderApp();
    const user = await openEditForm();
    await user.clear(screen.getByLabelText("Login"));
    await user.type(screen.getByLabelText("Login"), "new.login");
    await user.click(screen.getByRole("button", { name: "Enregistrer" }));
    await screen.findByText("Client mis à jour.");
    expect(sent).toEqual({ login: "new.login" });
  });

  it("shows the API error and keeps what was typed", async () => {
    stubFetch({ detail: ALPHA_WITH_ACCOUNT, onPatch: () => jsonResponse(409, { detail: "this login is already used" }) });
    renderApp();
    const user = await openEditForm();
    await user.clear(screen.getByLabelText("Login"));
    await user.type(screen.getByLabelText("Login"), "taken");
    await user.click(screen.getByRole("button", { name: "Enregistrer" }));
    expect(await screen.findByText("this login is already used")).toBeInTheDocument();
    expect(screen.getByLabelText("Login")).toHaveValue("taken");
    expect(screen.getByRole("button", { name: "Enregistrer" })).toBeInTheDocument();
  });

  it("returns to the normal view on Annuler without saving", async () => {
    const patch = vi.fn(() => jsonResponse(200, ALPHA_WITH_ACCOUNT));
    stubFetch({ detail: ALPHA_WITH_ACCOUNT, onPatch: patch });
    renderApp();
    const user = await openEditForm();
    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "Changed");
    await user.click(screen.getByRole("button", { name: "Annuler" }));
    expect(screen.getByRole("heading", { level: 2, name: "Alpha" })).toBeInTheDocument();
    expect(patch).not.toHaveBeenCalled();
  });
});

describe("AdminClientDetailPage Telegram bot", () => {
  it("shows a bot that is not set up yet, with no token field filled", async () => {
    stubFetch();
    renderApp();
    expect(await screen.findByText("Not configured")).toBeInTheDocument();
    expect(screen.getByLabelText("Bot token")).toHaveValue("");
  });

  it("reports a stored and connected bot from the stored credentials, never from a value", async () => {
    stubFetch({
      credentials: [
        { key: "telegram.bot_token", configured: true },
        { key: "telegram.webhook_secret", configured: true },
      ],
    });
    renderApp();
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("Configured")).toBeInTheDocument();
  });

  it("lets an owner test the bot with a read-only call and says so", async () => {
    const calls: string[] = [];
    stubFetch({ credentials: [{ key: "telegram.bot_token", configured: true }], calls });
    renderApp();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Test bot" }));
    expect(await screen.findByText(/Test passed\. telegram test ok/)).toBeInTheDocument();
    expect(calls).toContain("POST /clients/alpha/integrations/telegram/test");
    expect(calls.some((c) => c.includes("telegram/connect"))).toBe(false);
  });

  it("saves a token write-only and connects the bot", async () => {
    const calls: string[] = [];
    stubFetch({ calls });
    renderApp();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Bot token"), "123456:ABC-secret");
    await user.click(screen.getByRole("button", { name: "Save token" }));
    expect(await screen.findByText(/Connected\. telegram connect ok/)).toBeInTheDocument();
    expect(calls).toContain("PUT /clients/alpha/integrations/telegram.bot_token");
    expect(calls).toContain("POST /clients/alpha/integrations/telegram/connect");
    expect(screen.getByLabelText("Bot token")).toHaveValue("");
  });

  it("leaves the bot to an owner: another role cannot change it", async () => {
    stubFetch({ role: "editor", credentials: [{ key: "telegram.bot_token", configured: true }] });
    renderApp();
    expect(await screen.findByRole("button", { name: "Test bot" })).toBeDisabled();
    expect(screen.getByLabelText("Bot token")).toBeDisabled();
  });
});
