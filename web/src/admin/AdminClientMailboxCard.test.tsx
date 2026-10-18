import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AdminAuthProvider } from "./AdminAuthContext";
import { AdminClientMailboxCard } from "./pages/AdminClientMailboxCard";

function cred(key: string, label: string, over: Record<string, unknown> = {}) {
  return { key, label, scope: "client", sensitive: true, configured: false, value: null, updated_at: null, updated_by: null, ...over };
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function stub(opts: { role?: string; credentials?: unknown[]; state?: Record<string, unknown>; routes?: Record<string, () => Response> } = {}) {
  const { role = "owner", routes = {} } = opts;
  const credentials = opts.credentials ?? [
    cred("email.platform_mailbox", "Email mailbox", { sensitive: false }),
    cred("email.password", "Email password"),
  ];
  const state = opts.state ?? { state: "mailbox_not_assigned", smtp: null, imap: null };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const hit = routes[`${method} ${url}`];
    if (hit) return hit();
    if (url.endsWith("/auth/me")) return json(200, { username: "alice", role, client_ids: [] });
    if (url.endsWith("/clients/alpha/integrations") && method === "GET") return json(200, credentials);
    if (url.endsWith("/clients/alpha/integrations/email/status")) return json(200, state);
    throw new Error(`unexpected fetch ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderCard() {
  render(
    <MemoryRouter>
      <AdminAuthProvider>
        <AdminClientMailboxCard clientId="alpha" />
      </AdminAuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("AdminClientMailboxCard", () => {
  it.each([
    ["missing_configuration", "Email servers not configured"],
    ["mailbox_not_assigned", "No mailbox assigned"],
    ["password_missing", "Password missing"],
    ["not_authenticated", "Not authenticated"],
    ["ready", "Ready"],
  ])("shows the state %s", async (state, text) => {
    stub({ state: { state, smtp: null, imap: null } });
    renderCard();
    expect(await screen.findByText(text)).toBeInTheDocument();
  });

  it("assigns a mailbox address and stores its password write-only", async () => {
    const savedAddress = cred("email.platform_mailbox", "Email mailbox", { sensitive: false, configured: true, value: "box@mail.example.org" });
    const savedPassword = cred("email.password", "Email password", { configured: true });
    const fetchMock = stub({
      routes: {
        "PUT /admin/v1/clients/alpha/integrations/email.platform_mailbox": () => json(200, savedAddress),
        "PUT /admin/v1/clients/alpha/integrations/email.password": () => json(200, savedPassword),
      },
    });
    renderCard();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Mailbox address"), "box@mail.example.org");
    await user.click(screen.getAllByRole("button", { name: "Save" })[0]);
    expect(await screen.findByDisplayValue("box@mail.example.org")).toBeInTheDocument();
    const password = screen.getByLabelText("Mailbox password") as HTMLInputElement;
    expect(password.type).toBe("password");
    await user.type(password, "mailbox-secret-1");
    await user.click(screen.getAllByRole("button", { name: "Save" })[1]);
    expect(await screen.findAllByText("Saved.")).not.toHaveLength(0);
    expect((screen.getByLabelText("Mailbox password") as HTMLInputElement).value).toBe("");
    expect(document.body.textContent).not.toContain("mailbox-secret-1");
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(2);
  });

  it("shows a stored address and never a stored password", async () => {
    stub({
      credentials: [
        cred("email.platform_mailbox", "Email mailbox", { sensitive: false, configured: true, value: "box@mail.example.org" }),
        cred("email.password", "Email password", { configured: true }),
      ],
      state: { state: "not_authenticated", smtp: "not_tested", imap: "not_tested" },
    });
    renderCard();
    expect(await screen.findByDisplayValue("box@mail.example.org")).toBeInTheDocument();
    expect((screen.getByLabelText("Mailbox password") as HTMLInputElement).value).toBe("");
  });

  it("runs the login test, shows the verdict and refreshes the state", async () => {
    let reads = 0;
    stub({
      routes: {
        "GET /admin/v1/clients/alpha/integrations/email/status": () =>
          reads++ === 0
            ? json(200, { state: "not_authenticated", smtp: "not_tested", imap: "not_tested" })
            : json(200, { state: "ready", smtp: "authenticated", imap: "authenticated" }),
        "POST /admin/v1/clients/alpha/integrations/email/test": () =>
          json(200, { ok: true, message: "Email: both logins were accepted. No message was sent and none was read.", details: { smtp: "ok", imap: "ok" } }),
      },
    });
    renderCard();
    const user = userEvent.setup();
    expect(await screen.findByText("Not authenticated")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Test mailbox login" }));
    expect(await screen.findByText(/Login accepted\. Email: both logins were accepted/)).toBeInTheDocument();
    expect(await screen.findByText("Ready")).toBeInTheDocument();
  });

  it("reports a refused login as a failure", async () => {
    stub({
      routes: {
        "POST /admin/v1/clients/alpha/integrations/email/test": () =>
          json(200, { ok: false, message: "Email: SMTP: the server refused the login: check the username and the password.", details: { smtp: "auth" } }),
      },
    });
    renderCard();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Test mailbox login" }));
    expect(await screen.findByText(/Login failed\. Email: SMTP: the server refused the login/)).toBeInTheDocument();
  });

  it("logs in right after a save and shows each protocol with its reason", async () => {
    const savedPassword = cred("email.password", "Email password", { configured: true });
    let saved = false;
    const fetchMock = stub({
      credentials: [
        cred("email.platform_mailbox", "Email mailbox", { sensitive: false, configured: true, value: "box@mail.example.org" }),
        cred("email.password", "Email password"),
      ],
      routes: {
        "PUT /admin/v1/clients/alpha/integrations/email.password": () => {
          saved = true;
          return json(200, savedPassword);
        },
        "POST /admin/v1/clients/alpha/integrations/email/test": () =>
          json(200, { ok: false, message: "Email: SMTP: refused", details: { smtp: "auth", imap: "ok" } }),
        "GET /admin/v1/clients/alpha/integrations/email/status": () =>
          json(
            200,
            saved
              ? { state: "not_authenticated", smtp: "failed", imap: "authenticated", problem: "x", smtp_problem: "the server refused the login: check the username and the password", imap_problem: null }
              : { state: "password_missing", smtp: null, imap: null, problem: "the mailbox assigned to this client has no password", smtp_problem: null, imap_problem: null },
          ),
      },
    });
    renderCard();
    const user = userEvent.setup();
    expect(await screen.findByText("Password missing")).toBeInTheDocument();
    await user.type(await screen.findByLabelText("Mailbox password"), "mailbox-secret-1");
    await user.click(screen.getAllByRole("button", { name: "Save" })[1]);
    expect(await screen.findByText("Not authenticated")).toBeInTheDocument();
    expect(screen.getByText(/SMTP: failed — the server refused the login/)).toBeInTheDocument();
    expect(screen.getByText(/IMAP: authenticated/)).toBeInTheDocument();
    expect(screen.queryByText("Ready")).toBeNull();
    expect(fetchMock.mock.calls.some(([u, init]) => String(u).endsWith("/email/test") && init?.method === "POST")).toBe(true);
  });

  it("is read-only for a non-owner", async () => {
    stub({ role: "editor" });
    renderCard();
    expect(await screen.findByLabelText("Mailbox address")).toBeDisabled();
    expect(screen.getByLabelText("Mailbox password")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Test mailbox login" })).toBeDisabled();
  });
});
