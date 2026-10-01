import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { I18nProvider } from "../i18n";
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
  llm_model: "test-model",
  health: { status: "ready", last_error: null, last_error_at: null, consecutive_failures: 0, last_runs: {} },
};

const NAGA = {
  client_id: "naga",
  display_name: "Naga",
  status: "disabled",
  mode: "approval",
  timezone: "Africa/Tunis",
  daily_action_limit: 15,
  providers: { sheets: "google_sheets", llm: "openai", whatsapp: null },
  llm_model: "test-model",
  health: null,
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
  account: { login: "alpha.admin", whatsapp_number: "+216 22 768 307", logo: null, must_change_password: true },
  meta: { status: "not_connected", credentials_stored: false },
  revenue: null,
  prospects_contacted: 7,
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function stubFetch() {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { username: "alice", role: "owner", client_ids: [] });
      }
      if (url.endsWith("/settings/llm")) {
        return jsonResponse(200, { read_only: true, restricted: false, providers: [{ provider: "openai", wired: true, allowed_models: ["gpt-4o", "test-model"] }] });
      }
      if (url.endsWith("/clients") && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        if (body.login === "taken") return jsonResponse(409, { detail: "this login is already used" });
        return jsonResponse(201, { ...NAGA, client_id: "acme-corp", display_name: body.display_name, llm_model: body.llm_model, health: null });
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
    });
  vi.stubGlobal("fetch", mock);
  return mock;
}

function renderApp(initialPath = "/admin/clients") {
  render(
    <I18nProvider>
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
    </MemoryRouter>
    </I18nProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AdminClientsPage", () => {
  it("shows the clients in a table with the requested columns", async () => {
    stubFetch();
    renderApp();
    await screen.findByText("Alpha");
    const table = screen.getByRole("table");
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent?.trim());
    expect(headers.slice(0, 4)).toEqual(["Client", "LLM", "Daily quota", "Status"]);
    const naga = within(table).getByText("Naga").closest("tr") as HTMLElement;
    expect(within(naga).getByText("test-model")).toBeInTheDocument();
    expect(within(naga).getByText("15")).toBeInTheDocument();
    expect(within(naga).getByText("Inactive")).toBeInTheDocument();
    const alpha = within(table).getByText("Alpha").closest("tr") as HTMLElement;
    expect(within(alpha).getByText("Active")).toBeInTheDocument();
    expect(document.querySelector(".admin-client-card")).toBeNull();
  });

  it("has a View details link per row that opens the client's details", async () => {
    stubFetch();
    renderApp();
    await screen.findByText("Alpha");
    expect(screen.getAllByRole("link", { name: /View details/ })).toHaveLength(2);
    const user = userEvent.setup();
    await user.click(screen.getByRole("link", { name: "View details of Alpha" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Alpha" })).toBeInTheDocument();
  });

  it("opens an add-client form with the requested fields, WhatsApp number included, and no Meta credential", async () => {
    stubFetch();
    renderApp();
    await screen.findByText("Alpha");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "+ Ajouter un client" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Nom du client")).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/Logo/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Login")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Mot de passe temporaire")).toHaveAttribute("type", "password");
    expect(await within(dialog).findByLabelText("Modèle LLM")).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/Actif/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/Inactif/)).toBeChecked();
    expect(within(dialog).getByLabelText("WhatsApp number")).toBeInTheDocument();
    expect(within(dialog).queryByText(/access token|phone number id|app secret|verify token|api version/i)).not.toBeInTheDocument();
  });

  it("shows an empty Modèle LLM dropdown when Settings enables no model", async () => {
    const base = stubFetch();
    const impl = base.getMockImplementation()!;
    base.mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/settings/llm")) return jsonResponse(200, { read_only: true, restricted: false, providers: [{ provider: "openai", wired: true, allowed_models: null }] });
      return impl(input, init);
    });
    renderApp();
    await screen.findByText("Alpha");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "+ Ajouter un client" }));
    const select = await within(await screen.findByRole("dialog")).findByLabelText("Modèle LLM");
    expect(select.tagName).toBe("SELECT");
    expect(select).toBeDisabled();
  });

  it("creates a client and adds it to the table", async () => {
    const fetchMock = stubFetch();
    renderApp();
    await screen.findByText("Alpha");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "+ Ajouter un client" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Nom du client"), "Acme Corp");
    await user.type(within(dialog).getByLabelText("Login"), "acme.admin");
    await user.type(within(dialog).getByLabelText("Mot de passe temporaire"), "temp-pass-123");
    await user.selectOptions(await within(dialog).findByLabelText("Modèle LLM"), "gpt-4o");
    await user.type(within(dialog).getByLabelText("WhatsApp number"), "+216 22 768 307");
    await user.click(within(dialog).getByRole("button", { name: "Ajouter un client" }));
    expect(await screen.findByText("Acme Corp")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse(String((post?.[1] as RequestInit).body))).toEqual({
      display_name: "Acme Corp",
      logo: null,
      login: "acme.admin",
      temporary_password: "temp-pass-123",
      llm_provider: "openai",
      llm_model: "gpt-4o",
      status: "inactive",
      whatsapp_number: "+216 22 768 307",
    });
  });

  it("cannot be submitted without a WhatsApp number", async () => {
    stubFetch();
    renderApp();
    await screen.findByText("Alpha");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "+ Ajouter un client" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Nom du client"), "Acme Corp");
    await user.type(within(dialog).getByLabelText("Login"), "acme.admin");
    await user.type(within(dialog).getByLabelText("Mot de passe temporaire"), "temp-pass-123");
    await user.selectOptions(await within(dialog).findByLabelText("Modèle LLM"), "gpt-4o");
    expect(within(dialog).getByRole("button", { name: "Ajouter un client" })).toBeDisabled();
  });

  it("shows the server's refusal and keeps the form open", async () => {
    stubFetch();
    renderApp();
    await screen.findByText("Alpha");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "+ Ajouter un client" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Nom du client"), "Dup");
    await user.type(within(dialog).getByLabelText("Login"), "taken");
    await user.type(within(dialog).getByLabelText("Mot de passe temporaire"), "temp-pass-123");
    await user.selectOptions(await within(dialog).findByLabelText("Modèle LLM"), "gpt-4o");
    await user.type(within(dialog).getByLabelText("WhatsApp number"), "+216 22 768 307");
    await user.click(within(dialog).getByRole("button", { name: "Ajouter un client" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("this login is already used");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("AdminClientDetailPage", () => {
  it("shows the client's key information and no Meta credentials", async () => {
    stubFetch();
    renderApp("/admin/clients/alpha");
    await screen.findByRole("heading", { level: 2, name: "Alpha" });
    const card = screen.getByText("Client").closest(".admin-detail-card") as HTMLElement;
    expect(within(card).getByText("alpha.admin")).toBeInTheDocument();
    expect(within(card).getByText("Active")).toBeInTheDocument();
    expect(within(card).getByText("WhatsApp number")).toBeInTheDocument();
    expect(within(card).getByText("+216 22 768 307")).toBeInTheDocument();
    expect(within(card).getByText("Meta")).toBeInTheDocument();
    expect(within(card).getByText("Not connected")).toBeInTheDocument();
    expect(within(card).queryByText(/^Connected$/)).toBeNull();
    expect(screen.getByText("Daily quota").nextElementSibling).toHaveTextContent("20");
    expect(screen.getByText("Revenue").nextElementSibling).toHaveTextContent("—");
    expect(screen.getByText("Prospects contacted").nextElementSibling).toHaveTextContent("7");
    const llmCard = screen.getByText("LLM model", { selector: "h3" }).closest(".admin-detail-card") as HTMLElement;
    expect(llmCard.querySelector(".admin-detail-card__value")).toHaveTextContent("test-model");
    // No Meta credential or secret env name on the page.
    expect(document.body.textContent).not.toMatch(/ALPHA_TOKEN|ALPHA_PHONE_ID|ALPHA_APP_SECRET|OPENAI_API_KEY|Phone number ID|Access token|App secret|Verify token/i);
  });

  it("shows no agent or runtime internals", async () => {
    stubFetch();
    renderApp("/admin/clients/alpha");
    await screen.findByRole("heading", { level: 2, name: "Alpha" });
    expect(document.body.textContent).not.toMatch(/Agent ID|Brain|Timezone|Max attempts|Daily run|Wired|accepted for this provider/);
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
