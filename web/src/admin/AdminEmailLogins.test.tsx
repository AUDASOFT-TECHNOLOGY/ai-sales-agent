import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AdminAuthProvider } from "./AdminAuthContext";
import { AdminRoute } from "./AdminRoute";
import { AdminShell } from "./AdminShell";
import { AdminSettingsPage } from "./pages/AdminSettingsPage";

function cred(key: string, label: string, over: Record<string, unknown> = {}) {
  return { key, label, scope: "global", sensitive: false, configured: false, value: null, updated_at: null, updated_by: null, ...over };
}

const KEYS: [string, string, boolean][] = [
  ["email.platform_domain", "Platform mail domain", false],
  ["email.platform_smtp_host", "Platform SMTP host", false],
  ["email.platform_smtp_port", "Platform SMTP port", false],
  ["email.platform_smtp_security", "Platform SMTP security", false],
  ["email.platform_smtp_username", "Platform SMTP username", false],
  ["email.platform_smtp_password", "Platform SMTP password", true],
  ["email.platform_imap_host", "Platform IMAP host", false],
  ["email.platform_imap_port", "Platform IMAP port", false],
  ["email.platform_imap_security", "Platform IMAP security", false],
  ["email.platform_imap_username", "Platform IMAP username", false],
  ["email.platform_imap_password", "Platform IMAP password", true],
];

function list(over: Record<string, Record<string, unknown>> = {}) {
  return KEYS.map(([key, label, sensitive]) => cred(key, label, { sensitive, ...(over[key] ?? {}) }));
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const STATUS = "GET /admin/v1/settings/integrations/email/status";
const status = (state: string, smtp: string, imap: string, missing: string[] = []) => () =>
  json(200, { state, smtp, imap, missing });

function stub(opts: { role?: string; global?: unknown[]; routes?: Record<string, () => Response> } = {}) {
  const { role = "owner", global = list(), routes = {} } = opts;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const hit = routes[`${method} ${url}`];
    if (hit) return hit();
    if (url.endsWith("/auth/me")) return json(200, { username: "alice", role, client_ids: [] });
    if (url.endsWith("/settings/integrations") && method === "GET") return json(200, global);
    if (url.includes("/settings/openai/models")) return json(200, { models: [] });
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
          <Route path="/admin" element={<AdminRoute><AdminShell /></AdminRoute>}>
            <Route path="settings" element={<AdminSettingsPage />} />
          </Route>
        </Routes>
      </AdminAuthProvider>
    </MemoryRouter>,
  );
}

async function emailSection() {
  const heading = await screen.findByRole("heading", { name: "Email servers" });
  return within(heading.closest(".admin-settings__card") as HTMLElement);
}

afterEach(() => vi.unstubAllGlobals());

describe("Admin Email servers — logins", () => {
  it("shows a username and a password field for SMTP and for IMAP", async () => {
    stub();
    renderApp();
    const email = await emailSection();
    for (const label of ["SMTP username", "SMTP password", "IMAP username", "IMAP password"]) {
      expect(await email.findByLabelText(label)).toBeInTheDocument();
    }
    expect((email.getByLabelText("SMTP password") as HTMLInputElement).type).toBe("password");
    expect((email.getByLabelText("IMAP password") as HTMLInputElement).type).toBe("password");
    expect((email.getByLabelText("IMAP username") as HTMLInputElement).type).toBe("text");
  });

  it("shows a stored username but never a stored password, only that it is configured", async () => {
    stub({
      global: list({
        "email.platform_smtp_username": { configured: true, value: "login@example.org" },
        "email.platform_smtp_password": { configured: true },
      }),
    });
    renderApp();
    const email = await emailSection();
    const password = (await email.findByLabelText("SMTP password")) as HTMLInputElement;
    expect(password.value).toBe("");
    expect(password.placeholder).toMatch(/Configured/);
    expect((email.getByLabelText("SMTP username") as HTMLInputElement).value).toBe("login@example.org");
  });

  it("saves a password through the write-only endpoint, then empties the field", async () => {
    const saved = cred("email.platform_smtp_password", "Platform SMTP password", { sensitive: true, configured: true });
    const fetchMock = stub({
      routes: { "PUT /admin/v1/settings/integrations/email.platform_smtp_password": () => json(200, saved) },
    });
    renderApp();
    const user = userEvent.setup();
    const email = await emailSection();
    const field = (await email.findByLabelText("SMTP password")) as HTMLInputElement;
    await user.type(field, "s3cret-value");
    await user.click(within(field.closest("div.admin-settings__row") as HTMLElement).getByRole("button", { name: "Save" }));
    expect(await email.findByText("Saved.")).toBeInTheDocument();
    expect(field.value).toBe("");
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(put?.[1]?.body).toBe(JSON.stringify({ value: "s3cret-value" }));
    expect(document.body.textContent).not.toContain("s3cret-value");
  });

  it("tests SMTP and IMAP with separate buttons and tells accepted from refused", async () => {
    const fetchMock = stub({
      routes: {
        "POST /admin/v1/settings/integrations/email/test-smtp": () =>
          json(200, { ok: true, message: "Email SMTP: the server accepted the login. No message was sent and none was read.", details: { smtp: "ok" } }),
        "POST /admin/v1/settings/integrations/email/test-imap": () =>
          json(200, { ok: false, message: "Email IMAP: the server refused the login: check the username and the password.", details: { imap: "auth" } }),
      },
    });
    renderApp();
    const user = userEvent.setup();
    const email = await emailSection();
    await user.click(await email.findByRole("button", { name: "Test SMTP login" }));
    expect(await email.findByText(/Login accepted\. Email SMTP: the server accepted the login/)).toBeInTheDocument();
    await user.click(email.getByRole("button", { name: "Test IMAP login" }));
    expect(await email.findByText(/Login failed\. Email IMAP: the server refused the login/)).toBeInTheDocument();
    expect(email.getByText("imap: auth")).toBeInTheDocument();
    const urls = fetchMock.mock.calls.map(([u]) => String(u));
    expect(urls.some((u) => u.endsWith("/email/test-smtp"))).toBe(true);
    expect(urls.some((u) => u.endsWith("/email/test-imap"))).toBe(true);
  });

  it("does not present the plain connection test as an authentication", async () => {
    stub({
      routes: {
        "POST /admin/v1/settings/integrations/email/test": () =>
          json(200, { ok: true, message: "Email servers: both answered over an encrypted connection. No login was attempted and no message was sent.", details: { smtp: "ok", imap: "ok" } }),
      },
    });
    renderApp();
    const user = userEvent.setup();
    const email = await emailSection();
    await user.click(await email.findByRole("button", { name: "Test servers" }));
    expect(await email.findByText(/No login was attempted/)).toBeInTheDocument();
    expect(email.queryByText(/Login accepted/)).toBeNull();
  });

  it.each([
    ["missing_configuration", "not_configured", "not_configured", "Configuration missing"],
    ["server_not_authenticated", "authenticated", "not_tested", "Servers not authenticated"],
    ["ready", "authenticated", "authenticated", "Ready"],
  ])("shows the service state %s", async (state, smtp, imap, text) => {
    stub({ routes: { [STATUS]: status(state, smtp, imap, state === "missing_configuration" ? ["SMTP host"] : []) } });
    renderApp();
    const email = await emailSection();
    expect(await email.findByText(text)).toBeInTheDocument();
    if (state === "missing_configuration") expect(await email.findByText(/Missing: SMTP host/)).toBeInTheDocument();
  });

  it("refreshes the state after a test", async () => {
    let calls = 0;
    stub({
      routes: {
        [STATUS]: () => (calls++ === 0 ? status("server_not_authenticated", "not_tested", "not_tested")() : status("ready", "authenticated", "authenticated")()),
        "POST /admin/v1/settings/integrations/email/test-smtp": () => json(200, { ok: true, message: "ok", details: { smtp: "ok" } }),
      },
    });
    renderApp();
    const user = userEvent.setup();
    const email = await emailSection();
    expect(await email.findByText("Servers not authenticated")).toBeInTheDocument();
    await user.click(email.getByRole("button", { name: "Test SMTP login" }));
    expect(await email.findByText("Ready")).toBeInTheDocument();
  });

  it("is read-only for a non-owner", async () => {
    stub({ role: "editor" });
    renderApp();
    const email = await emailSection();
    expect(await email.findByLabelText("SMTP password")).toBeDisabled();
    expect(await email.findByLabelText("IMAP username")).toBeDisabled();
  });
});

describe("Admin Email servers — Save checks the logins", () => {
  const SAVED = cred("email.platform_smtp_host", "Platform SMTP host", { configured: true, value: "smtp.example.org" });

  it("checks SMTP then IMAP right after a save, and shows the proof, not the fields", async () => {
    const order: string[] = [];
    let saved = false;
    stub({
      routes: {
        "PUT /admin/v1/settings/integrations/email.platform_smtp_host": () => {
          saved = true;
          return json(200, SAVED);
        },
        "POST /admin/v1/settings/integrations/email/test-smtp": () => {
          order.push("smtp");
          return json(200, { ok: true, message: "ok", details: { smtp: "ok" } });
        },
        "POST /admin/v1/settings/integrations/email/test-imap": () => {
          order.push("imap");
          return json(200, { ok: false, message: "no", details: { imap: "auth" } });
        },
        [STATUS]: () =>
          saved
            ? json(200, {
                state: "server_not_authenticated",
                smtp: "authenticated",
                imap: "failed",
                missing: [],
                smtp_problem: null,
                imap_problem: "the server refused the login: check the username and the password",
              })
            : json(200, { state: "missing_configuration", smtp: "not_configured", imap: "not_configured", missing: ["SMTP host"], smtp_problem: null, imap_problem: null }),
      },
    });
    renderApp();
    const user = userEvent.setup();
    const email = await emailSection();
    const field = (await email.findByLabelText("SMTP host")) as HTMLInputElement;
    await user.type(field, "smtp.example.org");
    await user.click(within(field.closest("div.admin-settings__row") as HTMLElement).getByRole("button", { name: "Save" }));
    expect(await email.findByText("Servers not authenticated")).toBeInTheDocument();
    expect(order).toEqual(["smtp", "imap"]);
    // The two protocols are told apart, with the reason for the one that failed.
    expect(email.getByText(/SMTP: authenticated/)).toBeInTheDocument();
    expect(email.getByText(/IMAP: login failed — the server refused the login/)).toBeInTheDocument();
    expect(email.queryByText("Ready")).toBeNull();
  });

  it("is Ready only when the server says both logins were accepted", async () => {
    stub({
      routes: {
        [STATUS]: () => json(200, { state: "ready", smtp: "authenticated", imap: "authenticated", missing: [], smtp_problem: null, imap_problem: null }),
      },
    });
    renderApp();
    const email = await emailSection();
    expect(await email.findByText("Ready")).toBeInTheDocument();
    expect(email.getByText(/SMTP: authenticated/)).toBeInTheDocument();
    expect(email.getByText(/IMAP: authenticated/)).toBeInTheDocument();
  });

  it("shows why a server is not configured", async () => {
    stub({
      routes: {
        [STATUS]: () =>
          json(200, {
            state: "missing_configuration",
            smtp: "not_configured",
            imap: "not_configured",
            missing: ["IMAP host"],
            smtp_problem: "the SMTP username or password is not set yet",
            imap_problem: "the server settings are not complete yet (IMAP host)",
          }),
      },
    });
    renderApp();
    const email = await emailSection();
    expect(await email.findByText(/SMTP: not configured — the SMTP username or password is not set yet/)).toBeInTheDocument();
    expect(email.getByText(/Missing: IMAP host/)).toBeInTheDocument();
  });
});

describe("Admin Email servers — Email for new clients", () => {
  const ready = { state: "ready", smtp: "authenticated", imap: "authenticated", missing: [], smtp_problem: null, imap_problem: null };

  it("shows it off, and cannot be turned on while the servers are not proven", async () => {
    stub({
      routes: {
        [STATUS]: () =>
          json(200, { ...ready, state: "server_not_authenticated", smtp: "authenticated", imap: "not_tested", new_clients_access: false, clients_awaiting_mailbox: [] }),
      },
    });
    renderApp();
    const email = await emailSection();
    expect(await email.findByText("Email off")).toBeInTheDocument();
    expect(email.getByRole("button", { name: "Turn on for new clients" })).toBeDisabled();
  });

  it("turns it on once both logins are accepted, and says existing clients are unchanged", async () => {
    let on = false;
    const fetchMock = stub({
      routes: {
        [STATUS]: () => json(200, { ...ready, new_clients_access: on, clients_awaiting_mailbox: [] }),
        "PUT /admin/v1/settings/integrations/email/new-clients": () => {
          on = true;
          return json(200, { ...ready, new_clients_access: true, clients_awaiting_mailbox: [] });
        },
      },
    });
    renderApp();
    const user = userEvent.setup();
    const email = await emailSection();
    expect(await email.findByText(/Clients that already exist are not changed/)).toBeInTheDocument();
    await user.click(await email.findByRole("button", { name: "Turn on for new clients" }));
    expect(await email.findByText("Email on")).toBeInTheDocument();
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT" && String(fetchMock.mock.calls[0][0]).length > 0);
    expect(String(put?.[0])).toMatch(/email\/new-clients$/);
    expect(put?.[1]?.body).toBe(JSON.stringify({ enabled: true }));
    expect(email.getByRole("button", { name: "Turn off for new clients" })).toBeInTheDocument();
  });

  it("shows the server's refusal", async () => {
    stub({
      routes: {
        [STATUS]: () => json(200, { ...ready, new_clients_access: false, clients_awaiting_mailbox: [] }),
        "PUT /admin/v1/settings/integrations/email/new-clients": () =>
          json(409, { detail: "the email servers are not ready: save the settings and test both logins first" }),
      },
    });
    renderApp();
    const user = userEvent.setup();
    const email = await emailSection();
    await user.click(await email.findByRole("button", { name: "Turn on for new clients" }));
    expect(await email.findByText(/the email servers are not ready/)).toBeInTheDocument();
  });

  it("lists the clients waiting for a mailbox", async () => {
    stub({
      routes: {
        [STATUS]: () => json(200, { ...ready, new_clients_access: true, clients_awaiting_mailbox: ["acme-corp", "zed-co"] }),
      },
    });
    renderApp();
    const email = await emailSection();
    expect(await email.findByText(/Waiting for a mailbox \(2\): acme-corp, zed-co/)).toBeInTheDocument();
  });

  it("only an owner can change it", async () => {
    stub({
      role: "editor",
      routes: { [STATUS]: () => json(200, { ...ready, new_clients_access: true, clients_awaiting_mailbox: [] }) },
    });
    renderApp();
    const email = await emailSection();
    expect(await email.findByRole("button", { name: "Turn off for new clients" })).toBeDisabled();
  });
});
