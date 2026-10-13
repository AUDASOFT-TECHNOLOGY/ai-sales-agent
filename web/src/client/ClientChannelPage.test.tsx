import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { ClientChannelPage } from "./ClientChannelPage";

const SECRET = "hunter2-very-secret";

function channel(over: Record<string, unknown> = {}) {
  return {
    version: 3,
    channel: "email",
    authorized: true,
    options: [
      { key: "email", label: "Email" },
      { key: "telegram", label: "Telegram" },
    ],
    email: {
      from_address: "sales@acme.test",
      from_name: null,
      username: "sales@acme.test",
      smtp_host: "smtp.acme.test",
      smtp_port: 587,
      smtp_security: "starttls",
      imap_host: "imap.acme.test",
      imap_port: 993,
      imap_security: "ssl",
      mailbox: "INBOX",
      subject: "Hello",
    },
    telegram: null,
    credential: { label: "Mailbox password", configured: false, updated_at: null },
    problem: "Add your mailbox password.",
    updated_at: null,
    updated_by: null,
    ...over,
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Handler = (init: RequestInit | undefined) => Response | Promise<Response>;

function stub(routes: Record<string, Handler>) {
  const calls: Array<{ key: string; body: string | undefined }> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(input).replace("/client/v1", "")}`;
    calls.push({ key, body: init?.body as string | undefined });
    const handler = routes[key];
    if (!handler) throw new Error(`unexpected fetch ${key}`);
    return handler(init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

function renderPage() {
  render(
    <I18nProvider>
      <MemoryRouter>
        <ClientChannelPage />
      </MemoryRouter>
    </I18nProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("ClientChannelPage", () => {
  it("lists only the channels the Admin allows and says when none is", async () => {
    stub({ "GET /channel": () => json(200, channel({ options: [], channel: "whatsapp", problem: null })) });
    renderPage();
    expect(await screen.findByText(/No channel is available for your account/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save settings" })).not.toBeInTheDocument();
  });

  it("shows a stored secret as stored, never its value, and empties the field on save", async () => {
    let stored = false;
    const calls = stub({
      "GET /channel": () => json(200, channel({ credential: { label: "x", configured: stored, updated_at: null } })),
      "PUT /channel/credentials": () => {
        stored = true;
        return json(200, { label: "x", configured: true, updated_at: null });
      },
    });
    renderPage();
    expect(await screen.findByTestId("secret-status")).toHaveTextContent("No secret stored yet.");
    const user = userEvent.setup();
    const field = screen.getByLabelText("Mailbox password");
    await user.type(field, SECRET);
    await user.click(screen.getByRole("button", { name: "Save secret" }));
    await waitFor(() => expect(screen.getByTestId("secret-status")).toHaveTextContent("A secret is stored."));
    expect(screen.getByLabelText("Mailbox password")).toHaveValue("");
    expect(document.body.textContent).not.toContain(SECRET);
    expect(calls.find((c) => c.key === "PUT /channel/credentials")?.body).toBe(JSON.stringify({ password: SECRET }));
  });

  it("saves the settings with the version they were based on", async () => {
    const calls = stub({
      "GET /channel": () => json(200, channel()),
      "PUT /channel": () => json(200, channel({ version: 4 })),
    });
    renderPage();
    const user = userEvent.setup();
    const subject = await screen.findByLabelText("Default subject");
    await user.clear(subject);
    await user.type(subject, "Hi there");
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText("Your settings have been saved.")).toBeInTheDocument();
    const sent = JSON.parse(calls.find((c) => c.key === "PUT /channel")!.body!) as Record<string, unknown>;
    expect(sent.expected_version).toBe(3);
    expect(sent.channel).toBe("email");
    expect((sent.email as Record<string, unknown>).subject).toBe("Hi there");
    expect((sent.email as Record<string, unknown>).smtp_port).toBe(587);
  });

  it("refuses to test before the settings and secret are saved", async () => {
    const calls = stub({ "GET /channel": () => json(200, channel()) });
    renderPage();
    const button = await screen.findByRole("button", { name: "Test the connection" });
    expect(button).toBeDisabled();
    expect(calls.some((c) => c.key === "POST /channel/test")).toBe(false);
  });

  it("shows exactly what the real test answered: a failure is a failure", async () => {
    stub({
      "GET /channel": () => json(200, channel({ credential: { label: "x", configured: true, updated_at: null }, problem: null })),
      "POST /channel/test": () =>
        json(200, { channel: "email", ok: false, message: "The mailbox refused the login.", details: { stage: "imap" }, checked_at: "2026-10-13T10:00:00Z" }),
    });
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Test the connection" }));
    const result = await screen.findByTestId("test-result");
    expect(result).toHaveTextContent("Connection failed.");
    expect(result).toHaveTextContent("The mailbox refused the login.");
    expect(result).not.toHaveTextContent("Connection works.");
  });

  it("shows a successful real test as such", async () => {
    stub({
      "GET /channel": () => json(200, channel({ credential: { label: "x", configured: true, updated_at: null }, problem: null })),
      "POST /channel/test": () => json(200, { channel: "email", ok: true, message: "Logged in.", details: {}, checked_at: "2026-10-13T10:00:00Z" }),
    });
    renderPage();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Test the connection" }));
    expect(await screen.findByTestId("test-result")).toHaveTextContent("Connection works.");
  });

  it("explains a refusal (rate limit, forbidden channel, stale settings)", async () => {
    stub({
      "GET /channel": () => json(200, channel({ credential: { label: "x", configured: true, updated_at: null }, problem: null })),
      "POST /channel/test": () => json(429, { detail: "a test was just made: wait a few seconds" }),
    });
    renderPage();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Test the connection" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A test was just made.");
  });

  it("flags a channel that is not configured yet and one the Admin took away", async () => {
    stub({ "GET /channel": () => json(200, channel({ authorized: false })) });
    renderPage();
    expect(await screen.findByTestId("channel-status")).toHaveTextContent("no longer available for your account");
  });

  it("offers to connect a Telegram bot only once its token is stored", async () => {
    const calls = stub({
      "GET /channel": () =>
        json(
          200,
          channel({
            channel: "telegram",
            email: null,
            telegram: { contact_prompt: "p", contact_button: "b", not_found_text: "n", connected: false },
            credential: { label: "Telegram bot token", configured: true, updated_at: null },
            problem: "Connect your bot.",
          }),
        ),
      "POST /channel/connect": () => json(200, { channel: "telegram", ok: true, message: "Connected.", details: {}, checked_at: "2026-10-13T10:00:00Z" }),
    });
    renderPage();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Connect the bot" }));
    expect(await screen.findByTestId("test-result")).toHaveTextContent("Connection works.");
    expect(calls.some((c) => c.key === "POST /channel/connect")).toBe(true);
  });

  it("switches to Telegram without sending empty sentences, which the server would refuse", async () => {
    const calls = stub({
      "GET /channel": () => json(200, channel()),
      "PUT /channel": () =>
        json(200, channel({ channel: "telegram", email: null, telegram: { contact_prompt: "p", contact_button: "b", not_found_text: "n", connected: false }, credential: { label: "t", configured: false, updated_at: null } })),
    });
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: "Telegram" }));
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText(/Your channel has been changed/)).toBeInTheDocument();
    const sent = JSON.parse(calls.find((c) => c.key === "PUT /channel")!.body!) as Record<string, unknown>;
    expect(sent.channel).toBe("telegram");
    expect(sent.telegram).toEqual({});
    expect(sent.email).toBeUndefined();
  });

  it("reports a load failure and lets the client retry", async () => {
    stub({ "GET /channel": () => json(503, { detail: "settings are temporarily unavailable" }) });
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
