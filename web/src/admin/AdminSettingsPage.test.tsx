import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AdminAuthProvider } from "./AdminAuthContext";
import { AdminRoute } from "./AdminRoute";
import { AdminShell } from "./AdminShell";
import { AdminSettingsPage } from "./pages/AdminSettingsPage";

function cred(key: string, label: string, over: Record<string, unknown> = {}, scope = "global") {
  return {
    key,
    label,
    scope,
    sensitive: true,
    configured: false,
    value: null,
    updated_at: null,
    updated_by: null,
    ...over,
  };
}

const NOT_SENSITIVE = { sensitive: false };

function globalList(over: Record<string, Record<string, unknown>> = {}) {
  const base: [string, string, Record<string, unknown>][] = [
    ["openai.api_key", "OpenAI API key", {}],
    ["openai.organization", "OpenAI organization", NOT_SENSITIVE],
    ["openai.project", "OpenAI project", NOT_SENSITIVE],
    ["openai.base_url", "OpenAI base URL", NOT_SENSITIVE],
    ["whatsapp.verify_token", "WhatsApp verify token", {}],
    ["whatsapp.app_secret", "Meta app secret", {}],
    ["whatsapp.api_version", "WhatsApp API version", NOT_SENSITIVE],
  ];
  return base.map(([key, label, extra]) => cred(key, label, { ...extra, ...(over[key] ?? {}) }));
}

const CLIENT_LIST = [
  cred("whatsapp.access_token", "WhatsApp access token", {}, "client"),
  cred("whatsapp.phone_number_id", "WhatsApp phone number id", NOT_SENSITIVE, "client"),
];

const CLIENTS = [{ client_id: "alpha", display_name: "Alpha", status: "active" }];

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

interface Model {
  name: string;
  id: string;
  active: boolean;
}

interface Stub {
  role?: string;
  global?: unknown[];
  models?: Model[];
  routes?: Record<string, () => Response>;
}

const LUNA: Model = { name: "Luna", id: "model-luna", active: true };
const SOL: Model = { name: "Sol", id: "model-sol", active: false };

function stubFetch({ role = "owner", global = globalList(), models = [], routes = {} }: Stub = {}) {
  let list = models;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    const hit = routes[`${method} ${url}`];
    if (hit) return hit();
    if (url.endsWith("/auth/me")) return jsonResponse(200, { username: "alice", role, client_ids: [] });
    if (url.endsWith("/settings/integrations") && method === "GET") return jsonResponse(200, global);
    if (url.includes("/settings/openai/models")) {
      const id = decodeURIComponent(url.split("/settings/openai/models")[1].replace(/^\//, ""));
      const body = init?.body ? (JSON.parse(String(init.body)) as Model) : null;
      if (method === "POST" && body) list = [...list, body];
      if (method === "PUT" && body) list = list.map((m) => (m.id === id ? body : m));
      if (method === "DELETE") list = list.filter((m) => m.id !== id);
      return jsonResponse(200, { models: list });
    }
    if (url.endsWith("/clients") && method === "GET") return jsonResponse(200, CLIENTS);
    if (url.endsWith("/clients/alpha/integrations") && method === "GET") return jsonResponse(200, CLIENT_LIST);
    throw new Error(`unexpected fetch ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderApp() {
  render(
    <MemoryRouter initialEntries={["/admin/settings"]}>
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
            <Route path="settings" element={<AdminSettingsPage />} />
          </Route>
        </Routes>
      </AdminAuthProvider>
    </MemoryRouter>,
  );
}

async function section(name: string) {
  const heading = await screen.findByRole("heading", { name });
  return within(heading.closest(".admin-settings__card") as HTMLElement);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AdminSettingsPage", () => {
  it("shows the OpenAI fields, the model list and the Revansa-wide Meta fields only", async () => {
    stubFetch();
    renderApp();
    const openai = await section("OpenAI API");
    expect(await openai.findByLabelText("API Key")).toBeInTheDocument();
    for (const label of ["Organization", "Project", "Base URL"]) {
      expect(openai.queryByLabelText(label)).toBeNull();
    }
    expect(await openai.findByText("Available OpenAI Models")).toBeInTheDocument();
    expect(openai.getByText("These are the OpenAI models that can be selected for a client in Client Details.")).toBeInTheDocument();
    const meta = await section("Meta / WhatsApp API");
    for (const label of ["App Secret", "Verify Token", "API Version"]) {
      expect(await meta.findByLabelText(label)).toBeInTheDocument();
    }
    // A client's token and phone number id belong to its WhatsApp onboarding, not to Settings.
    for (const label of ["Access Token", "Phone Number ID"]) {
      expect(meta.queryByLabelText(label)).toBeNull();
    }
    expect(screen.queryByRole("button", { name: "Test WhatsApp connection" })).toBeNull();
  });

  it("never shows a saved secret: only that it is configured, in a password field", async () => {
    stubFetch({ global: globalList({ "openai.api_key": { configured: true } }) });
    renderApp();
    const key = (await (await section("OpenAI API")).findByLabelText("API Key")) as HTMLInputElement;
    expect(key.type).toBe("password");
    expect(key.value).toBe("");
    expect(key.placeholder).toMatch(/Configured/);
  });

  it("shows Not configured / Configured for the OpenAI connection", async () => {
    stubFetch();
    renderApp();
    const openai = await section("OpenAI API");
    expect(await openai.findByText("Not configured")).toBeInTheDocument();
    expect(openai.getByText("Connection status")).toBeInTheDocument();
  });

  it("shows Configured once the key is saved", async () => {
    stubFetch({ global: globalList({ "openai.api_key": { configured: true } }) });
    renderApp();
    const openai = await section("OpenAI API");
    expect((await openai.findAllByText("Configured")).length).toBeGreaterThan(0);
    expect(openai.queryByText("Not configured")).toBeNull();
  });

  it("says so, and invents nothing, when no model is configured", async () => {
    stubFetch();
    renderApp();
    const openai = await section("OpenAI API");
    expect(await openai.findByText("No OpenAI models configured.")).toBeInTheDocument();
    expect(openai.getByRole("button", { name: "+ Add model" })).toBeInTheDocument();
    expect(openai.queryByRole("table")).toBeNull();
  });

  it("lists the configured models with name, id and status", async () => {
    stubFetch({ models: [LUNA, SOL] });
    renderApp();
    const openai = await section("OpenAI API");
    const table = await openai.findByRole("table");
    for (const header of ["Model", "Model ID", "Status", "Actions"]) {
      expect(within(table).getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
    const luna = within(within(table).getByText("model-luna").closest("tr") as HTMLElement);
    expect(luna.getByText("Luna")).toBeInTheDocument();
    expect(luna.getByText("Active")).toBeInTheDocument();
    expect(within(within(table).getByText("model-sol").closest("tr") as HTMLElement).getByText("Inactive")).toBeInTheDocument();
  });

  it("adds a model", async () => {
    const fetchMock = stubFetch();
    renderApp();
    const user = userEvent.setup();
    const openai = await section("OpenAI API");
    await user.click(await openai.findByRole("button", { name: "+ Add model" }));
    await user.type(openai.getByLabelText("Model name"), "Luna");
    await user.type(openai.getByLabelText("Model ID"), "model-luna");
    await user.click(openai.getByRole("button", { name: "Add model" }));
    expect(await openai.findByText("model-luna")).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual(LUNA);
  });

  it("edits a model", async () => {
    const fetchMock = stubFetch({ models: [LUNA] });
    renderApp();
    const user = userEvent.setup();
    const openai = await section("OpenAI API");
    await user.click(await openai.findByRole("button", { name: "Edit Luna" }));
    const name = openai.getByLabelText("Model name");
    await user.clear(name);
    await user.type(name, "Luna 2");
    await user.click(openai.getByRole("button", { name: "Save model" }));
    expect(await openai.findByText("Luna 2")).toBeInTheDocument();
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(put?.[0]).toMatch(/\/settings\/openai\/models\/model-luna$/);
    expect(JSON.parse(String(put?.[1]?.body))).toEqual({ ...LUNA, name: "Luna 2" });
  });

  it("activates and deactivates a model", async () => {
    stubFetch({ models: [LUNA] });
    renderApp();
    const user = userEvent.setup();
    const openai = await section("OpenAI API");
    await user.click(await openai.findByRole("button", { name: "Deactivate Luna" }));
    expect(await openai.findByText("Inactive")).toBeInTheDocument();
    await user.click(openai.getByRole("button", { name: "Activate Luna" }));
    expect(await openai.findByText("Active")).toBeInTheDocument();
  });

  it("deletes a model after confirmation", async () => {
    vi.stubGlobal("confirm", () => true);
    stubFetch({ models: [LUNA] });
    renderApp();
    const user = userEvent.setup();
    const openai = await section("OpenAI API");
    await user.click(await openai.findByRole("button", { name: "Delete Luna" }));
    expect(await openai.findByText("No OpenAI models configured.")).toBeInTheDocument();
  });

  it("tells which clients use a model that cannot be deleted", async () => {
    vi.stubGlobal("confirm", () => true);
    stubFetch({
      models: [LUNA],
      routes: {
        "DELETE /admin/v1/settings/openai/models/model-luna": () =>
          jsonResponse(409, {
            detail: {
              message: "clients use this model: assign them another model first",
              client_ids: ["alpha", "beta"],
              models: { "model-luna": ["alpha", "beta"] },
            },
          }),
      },
    });
    renderApp();
    const user = userEvent.setup();
    const openai = await section("OpenAI API");
    await user.click(await openai.findByRole("button", { name: "Delete Luna" }));
    expect(
      await openai.findByText(/assign them another model first \(clients: alpha, beta\)/),
    ).toBeInTheDocument();
    expect(openai.getByText("Luna")).toBeInTheDocument(); // the model is still listed
  });

  it("saves a secret through the credential endpoint and clears the field", async () => {
    const saved = cred("openai.api_key", "OpenAI API key", { configured: true });
    const fetchMock = stubFetch({ routes: { "PUT /admin/v1/settings/integrations/openai.api_key": () => jsonResponse(200, saved) } });
    renderApp();
    const user = userEvent.setup();
    const openai = await section("OpenAI API");
    const input = (await openai.findByLabelText("API Key")) as HTMLInputElement;
    await user.type(input, "sk-typed-value");
    await user.click(openai.getAllByRole("button", { name: "Save" })[0]);
    expect(await openai.findAllByText("Configured")).not.toHaveLength(0);
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(put?.[1]?.body).toBe(JSON.stringify({ value: "sk-typed-value" }));
    expect(input.value).toBe("");
    expect(document.body.textContent).not.toContain("sk-typed-value");
  });

  it("runs the real OpenAI test and reports failure honestly", async () => {
    stubFetch({
      routes: {
        "POST /admin/v1/settings/integrations/openai/test": () =>
          jsonResponse(200, { ok: false, message: "OpenAI refused these credentials: the service answered 401.", details: {} }),
      },
    });
    renderApp();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Test connection" }));
    expect(await screen.findByText(/Connection failed\. OpenAI refused/)).toBeInTheDocument();
  });

  it("states that Muse Spark is not implemented and offers no field for it", async () => {
    stubFetch();
    renderApp();
    const muse = await section("Muse Spark (Meta LLM)");
    expect(await muse.findByText("Not implemented")).toBeInTheDocument();
    expect(muse.queryByRole("textbox")).toBeNull();
    expect(muse.queryByRole("button")).toBeNull();
  });

  it("is read-only for a role other than owner", async () => {
    stubFetch({ role: "editor", models: [LUNA] });
    renderApp();
    const openai = await section("OpenAI API");
    expect(await openai.findByLabelText("API Key")).toBeDisabled();
    expect(openai.getByRole("button", { name: "+ Add model" })).toBeDisabled();
    expect(openai.getByRole("button", { name: "Edit Luna" })).toBeDisabled();
    expect(screen.getByRole("note")).toHaveTextContent(/Only an owner/);
  });
});

const EMAIL_KEYS: [string, string][] = [
  ["email.platform_domain", "Platform mail domain"],
  ["email.platform_smtp_host", "Platform SMTP host"],
  ["email.platform_smtp_port", "Platform SMTP port"],
  ["email.platform_smtp_security", "Platform SMTP security"],
  ["email.platform_imap_host", "Platform IMAP host"],
  ["email.platform_imap_port", "Platform IMAP port"],
  ["email.platform_imap_security", "Platform IMAP security"],
];

function emailList(over: Record<string, Record<string, unknown>> = {}) {
  return EMAIL_KEYS.map(([key, label]) => cred(key, label, { sensitive: false, ...(over[key] ?? {}) }));
}

describe("AdminSettingsPage — Email servers", () => {
  it("shows the seven server settings, with the stored values since none is secret", async () => {
    stubFetch({
      global: [
        ...globalList(),
        ...emailList({ "email.platform_smtp_host": { configured: true, value: "smtp.example.org" } }),
      ],
    });
    renderApp();
    const email = await section("Email servers");
    for (const label of ["Mail domain", "SMTP host", "SMTP port", "SMTP security", "IMAP host", "IMAP port", "IMAP security"]) {
      expect(await email.findByLabelText(label)).toBeInTheDocument();
    }
    const host = email.getByLabelText("SMTP host") as HTMLInputElement;
    expect(host.type).toBe("text");
    expect(host.value).toBe("smtp.example.org");
    expect((email.getByLabelText("SMTP port") as HTMLInputElement).type).toBe("text");
  });

  it("says mailboxes are assigned one by one, and promises nothing automatic", async () => {
    stubFetch({ global: [...globalList(), ...emailList()] });
    renderApp();
    const email = await section("Email servers");
    expect(await email.findByText(/Mailboxes are assigned to clients one by one by an administrator/)).toBeInTheDocument();
    const card = (await screen.findByRole("heading", { name: "Email servers" })).closest(".admin-settings__card") as HTMLElement;
    expect(card.textContent).not.toMatch(/automatic|provision/i);
  });

  it("saves a setting with the version-free credential endpoint", async () => {
    const saved = cred("email.platform_smtp_port", "Platform SMTP port", { sensitive: false, configured: true, value: "587" });
    const fetchMock = stubFetch({
      global: [...globalList(), ...emailList()],
      routes: { "PUT /admin/v1/settings/integrations/email.platform_smtp_port": () => jsonResponse(200, saved) },
    });
    renderApp();
    const user = userEvent.setup();
    const email = await section("Email servers");
    await user.type(await email.findByLabelText("SMTP port"), "587");
    const row = (email.getByLabelText("SMTP port").closest("div.admin-settings__row") as HTMLElement) ?? undefined;
    await user.click(within(row).getByRole("button", { name: "Save" }));
    expect(await email.findAllByText("Configured")).not.toHaveLength(0);
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(put?.[0]).toMatch(/\/settings\/integrations\/email\.platform_smtp_port$/);
    expect(put?.[1]?.body).toBe(JSON.stringify({ value: "587" }));
  });

  it("shows a refused value with the server's rule", async () => {
    stubFetch({
      global: [...globalList(), ...emailList()],
      routes: {
        "PUT /admin/v1/settings/integrations/email.platform_smtp_port": () =>
          jsonResponse(422, { detail: "Platform SMTP port must be 25, 465, 587 or 2525" }),
      },
    });
    renderApp();
    const user = userEvent.setup();
    const email = await section("Email servers");
    await user.type(await email.findByLabelText("SMTP port"), "80");
    const row = email.getByLabelText("SMTP port").closest("div.admin-settings__row") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Save" }));
    expect(await email.findByText(/must be 25, 465, 587 or 2525/)).toBeInTheDocument();
  });

  it("shows exactly what the check answered when the servers answer", async () => {
    stubFetch({
      global: [...globalList(), ...emailList()],
      routes: {
        "POST /admin/v1/settings/integrations/email/test": () =>
          jsonResponse(200, {
            ok: true,
            message: "Email servers: both answered over an encrypted connection. No login was attempted and no message was sent.",
            details: { smtp: "ok", imap: "ok" },
          }),
      },
    });
    renderApp();
    const user = userEvent.setup();
    const email = await section("Email servers");
    await user.click(await email.findByRole("button", { name: "Test servers" }));
    expect(await email.findByText(/Connection OK\. Email servers: both answered/)).toBeInTheDocument();
    expect(email.getByText("smtp: ok")).toBeInTheDocument();
    expect(email.getByText("imap: ok")).toBeInTheDocument();
    expect(email.getByText(/does not log in and sends or reads no message/)).toBeInTheDocument();
  });

  it("reports a failed check as a failure, with the server and the kind", async () => {
    stubFetch({
      global: [...globalList(), ...emailList()],
      routes: {
        "POST /admin/v1/settings/integrations/email/test": () =>
          jsonResponse(200, {
            ok: false,
            message: "Email servers: IMAP: this server cannot be used (it is not a public mail server).",
            details: { smtp: "ok", imap: "refused" },
          }),
      },
    });
    renderApp();
    const user = userEvent.setup();
    const email = await section("Email servers");
    await user.click(await email.findByRole("button", { name: "Test servers" }));
    expect(await email.findByText(/Connection failed\. Email servers: IMAP/)).toBeInTheDocument();
    expect(email.getByText("imap: refused")).toBeInTheDocument();
  });

  it("is read-only for a role other than owner", async () => {
    stubFetch({ role: "editor", global: [...globalList(), ...emailList()] });
    renderApp();
    const email = await section("Email servers");
    expect(await email.findByLabelText("SMTP host")).toBeDisabled();
  });
});
