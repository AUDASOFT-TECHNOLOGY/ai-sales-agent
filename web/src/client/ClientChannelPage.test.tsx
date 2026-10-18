import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { ClientChannelPage } from "./ClientChannelPage";

function channel(over: Record<string, unknown> = {}) {
  return {
    version: 3,
    channel: "email",
    authorized: true,
    options: [
      { key: "email", label: "Email" },
      { key: "telegram", label: "Telegram" },
    ],
    status: "waiting_platform",
    status_message: "Revansa is setting up this channel for you. There is nothing for you to do.",
    // The default fixture is a historical ("custom") mailbox: its Sender name is the client's own field.
    email: { mode: "custom", from_name: "Acme Sales", sender_name_confirmed: false, subject: "Hello" },
    telegram: null,
    sender_name_proposal: "Acme Shop",
    updated_at: null,
    updated_by: null,
    ...over,
  };
}

const TELEGRAM = { contact_prompt: "p", contact_button: "b", not_found_text: "n" };

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

const TECHNICAL = [/password/i, /token/i, /smtp/i, /imap/i, /webhook/i, /server/i, /\bport\b/i, /login/i, /secret/i, /connect the bot/i];

describe("ClientChannelPage", () => {
  it("lists only the channels the Admin allows and says when none is", async () => {
    stub({ "GET /channel": () => json(200, channel({ options: [], channel: "whatsapp", status: "ready", status_message: null })) });
    renderPage();
    expect(await screen.findByText(/No channel is available for your account/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save settings" })).not.toBeInTheDocument();
  });

  it("asks for nothing technical: only the wording of the messages, never a server, password or token", async () => {
    stub({ "GET /channel": () => json(200, channel()) });
    renderPage();
    await screen.findByLabelText("Default subject");
    expect(screen.getByLabelText("Sender name")).toBeInTheDocument();
    expect(document.querySelectorAll('input[type="password"]')).toHaveLength(0);
    for (const word of TECHNICAL) expect(document.body.textContent).not.toMatch(word);
    expect(screen.queryByRole("button", { name: /test|connect/i })).not.toBeInTheDocument();
  });

  it("asks for nothing technical on Telegram either", async () => {
    stub({
      "GET /channel": () =>
        json(200, channel({ channel: "telegram", email: null, telegram: TELEGRAM, status: "waiting_platform" })),
    });
    renderPage();
    await screen.findByLabelText("Message asking for the phone number");
    expect(document.querySelectorAll('input[type="password"]')).toHaveLength(0);
    for (const word of TECHNICAL) expect(document.body.textContent).not.toMatch(word);
  });

  it.each([
    ["ready", null, "Ready"],
    ["waiting_platform", "Revansa is setting up this channel for you. There is nothing for you to do.", "Waiting for the platform"],
    ["action_required", "This channel is no longer available for your account. Choose another channel.", "Action required"],
  ])("says where the channel stands: %s", async (status, message, label) => {
    stub({ "GET /channel": () => json(200, channel({ status, status_message: message })) });
    renderPage();
    const line = await screen.findByTestId("channel-status");
    expect(line).toHaveTextContent(label);
    if (message) expect(screen.getByText(message)).toBeInTheDocument();
    else expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("saves the wording with the version it was based on and sends nothing technical", async () => {
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
    expect(sent.email).toEqual({ from_name: "Acme Sales", subject: "Hi there" });
    expect(Object.keys(sent).sort()).toEqual(["channel", "email", "expected_version"]);
  });

  it("calls no route that stores, tests or connects anything", async () => {
    const calls = stub({
      "GET /channel": () => json(200, channel()),
      "PUT /channel": () => json(200, channel({ version: 4 })),
    });
    renderPage();
    const user = userEvent.setup();
    const subject = await screen.findByLabelText("Default subject");
    await user.type(subject, "!");
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    await screen.findByText("Your settings have been saved.");
    expect(calls.map((c) => c.key).sort()).toEqual(["GET /channel", "PUT /channel"]);
  });

  it("lists the prospects waiting for Telegram once the platform has connected the bot", async () => {
    stub({
      "GET /channel": () =>
        json(200, channel({ channel: "telegram", email: null, telegram: TELEGRAM, status: "ready", status_message: null })),
      "GET /channel/invitations": () =>
        json(200, {
          bot_username: "naga_bot",
          bot_link: "https://t.me/naga_bot",
          waiting: 1,
          invitations: [{ prospect_id: "p1", display_name: "Alex", phone: null, email: null, link: "https://t.me/naga_bot?start=x" }],
        }),
    });
    renderPage();
    expect(await screen.findByText("Alex")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "invitation link" })).toBeInTheDocument();
  });

  it("does not ask for the invitations while the platform has not finished", async () => {
    const calls = stub({
      "GET /channel": () => json(200, channel({ channel: "telegram", email: null, telegram: TELEGRAM })),
    });
    renderPage();
    await screen.findByTestId("channel-status");
    expect(calls.some((c) => c.key === "GET /channel/invitations")).toBe(false);
  });

  it("switches to Telegram without sending empty sentences, which the server would refuse", async () => {
    const calls = stub({
      "GET /channel": () => json(200, channel()),
      "PUT /channel": () => json(200, channel({ channel: "telegram", email: null, telegram: TELEGRAM })),
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

  it("explains a refusal without repeating anything technical", async () => {
    stub({
      "GET /channel": () => json(200, channel()),
      "PUT /channel": () => json(403, { detail: "this channel is not available for your account" }),
    });
    renderPage();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Default subject"), "!");
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This channel is not available for your account.");
  });

  it("reports a load failure and lets the client retry", async () => {
    stub({ "GET /channel": () => json(503, { detail: "settings are temporarily unavailable" }) });
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

const PLATFORM_UNCONFIRMED = { mode: "platform", from_name: null, sender_name_confirmed: false, subject: "Hello" };
const PLATFORM_CONFIRMED = { mode: "platform", from_name: "Acme Boutique", sender_name_confirmed: true, subject: "Hello" };

function sentBody(calls: Array<{ key: string; body: string | undefined }>) {
  return JSON.parse(calls.find((c) => c.key === "PUT /channel")!.body!) as { email: Record<string, string> };
}

describe("ClientChannelPage — Sender name by mailbox mode", () => {
  it("custom mode: the field is visible, holds the stored value and is saved as before", async () => {
    const calls = stub({
      "GET /channel": () => json(200, channel()),
      "PUT /channel": () => json(200, channel({ version: 4 })),
    });
    renderPage();
    const user = userEvent.setup();
    const name = (await screen.findByLabelText("Sender name")) as HTMLInputElement;
    expect(name.value).toBe("Acme Sales");
    expect(screen.queryByTestId("sender-preview")).toBeNull();
    await user.clear(name);
    await user.type(name, "Own Name");
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText("Your settings have been saved.")).toBeInTheDocument();
    expect(sentBody(calls).email).toEqual({ from_name: "Own Name", subject: "Hello" });
    expect(screen.queryByText("Confirm and save")).toBeNull();
  });

  it("platform mode: the business name is proposed in the field, not taken for a confirmation", async () => {
    stub({ "GET /channel": () => json(200, channel({ email: PLATFORM_UNCONFIRMED })) });
    renderPage();
    const name = (await screen.findByLabelText("Sender name")) as HTMLInputElement;
    expect(name.value).toBe("Acme Shop");
    expect(screen.getByText(/Pre-filled with your business name/)).toBeInTheDocument();
    // Nothing was typed, but the name still has to be confirmed.
    expect(screen.getByRole("button", { name: "Save settings" })).toBeEnabled();
  });

  it("platform mode: shows a preview and saves the name only once confirmed", async () => {
    const calls = stub({
      "GET /channel": () => json(200, channel({ email: PLATFORM_UNCONFIRMED })),
      "PUT /channel": () => json(200, channel({ version: 4, email: { ...PLATFORM_CONFIRMED, from_name: "Acme Studio" } })),
    });
    renderPage();
    const user = userEvent.setup();
    const name = await screen.findByLabelText("Sender name");
    await user.clear(name);
    await user.type(name, "Acme Studio");
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    expect(screen.getByTestId("sender-preview")).toHaveTextContent("Your emails will be sent as “Acme Studio”");
    expect(calls.some((c) => c.key === "PUT /channel")).toBe(false); // nothing saved before the confirmation
    await user.click(screen.getByRole("button", { name: "Confirm and save" }));
    expect(await screen.findByText("Your settings have been saved.")).toBeInTheDocument();
    expect(sentBody(calls).email.from_name).toBe("Acme Studio");
  });

  it("platform mode: confirming the proposed name as it is stores it", async () => {
    const calls = stub({
      "GET /channel": () => json(200, channel({ email: PLATFORM_UNCONFIRMED })),
      "PUT /channel": () => json(200, channel({ version: 4, email: { ...PLATFORM_CONFIRMED, from_name: "Acme Shop" } })),
    });
    renderPage();
    const user = userEvent.setup();
    await screen.findByLabelText("Sender name");
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    await user.click(screen.getByRole("button", { name: "Confirm and save" }));
    expect(await screen.findByText("Your settings have been saved.")).toBeInTheDocument();
    expect(sentBody(calls).email.from_name).toBe("Acme Shop");
  });

  it("platform mode: editing goes back from the preview without saving", async () => {
    const calls = stub({ "GET /channel": () => json(200, channel({ email: PLATFORM_UNCONFIRMED })) });
    renderPage();
    const user = userEvent.setup();
    await screen.findByLabelText("Sender name");
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    await user.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.queryByTestId("sender-preview")).toBeNull();
    expect(calls.some((c) => c.key === "PUT /channel")).toBe(false);
  });

  it("platform mode: a confirmed name is shown as stored, and a subject change alone leaves it out", async () => {
    const calls = stub({
      "GET /channel": () => json(200, channel({ email: PLATFORM_CONFIRMED })),
      "PUT /channel": () => json(200, channel({ version: 4, email: { ...PLATFORM_CONFIRMED, subject: "New" } })),
    });
    renderPage();
    const user = userEvent.setup();
    const name = (await screen.findByLabelText("Sender name")) as HTMLInputElement;
    expect(name.value).toBe("Acme Boutique"); // not the business name
    expect(screen.getByRole("button", { name: "Save settings" })).toBeDisabled();
    const subject = screen.getByLabelText("Default subject");
    await user.clear(subject);
    await user.type(subject, "New");
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    expect(screen.queryByTestId("sender-preview")).toBeNull(); // no confirmation for the subject
    expect(await screen.findByText("Your settings have been saved.")).toBeInTheDocument();
    expect(sentBody(calls).email).toEqual({ subject: "New" });
  });

  it("platform mode: a confirmed name can be changed later, with a new confirmation", async () => {
    const calls = stub({
      "GET /channel": () => json(200, channel({ email: PLATFORM_CONFIRMED })),
      "PUT /channel": () => json(200, channel({ version: 4, email: { ...PLATFORM_CONFIRMED, from_name: "Acme Group" } })),
    });
    renderPage();
    const user = userEvent.setup();
    const name = await screen.findByLabelText("Sender name");
    await user.clear(name);
    await user.type(name, "Acme Group");
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    expect(screen.getByTestId("sender-preview")).toHaveTextContent("“Acme Group”");
    await user.click(screen.getByRole("button", { name: "Confirm and save" }));
    expect(await screen.findByText("Your settings have been saved.")).toBeInTheDocument();
    expect(sentBody(calls).email.from_name).toBe("Acme Group");
  });

  it("platform mode: an empty name cannot be confirmed", async () => {
    stub({ "GET /channel": () => json(200, channel({ email: PLATFORM_UNCONFIRMED })) });
    renderPage();
    const user = userEvent.setup();
    const name = await screen.findByLabelText("Sender name");
    await user.clear(name);
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    expect(screen.queryByTestId("sender-preview")).toBeNull();
    expect(screen.getByText("Enter the name your emails should be sent under.")).toBeInTheDocument();
  });

  it("choosing email from another channel proposes the business name for the platform's mailbox", async () => {
    stub({ "GET /channel": () => json(200, channel({ channel: "telegram", email: null, telegram: TELEGRAM })) });
    renderPage();
    const user = userEvent.setup();
    await screen.findByTestId("channel-status");
    await user.click(screen.getByRole("radio", { name: "Email" }));
    expect(((await screen.findByLabelText("Sender name")) as HTMLInputElement).value).toBe("Acme Shop");
  });

  it("platform mode shows nothing technical", async () => {
    stub({ "GET /channel": () => json(200, channel({ email: PLATFORM_UNCONFIRMED })) });
    renderPage();
    await screen.findByLabelText("Sender name");
    const text = document.body.textContent ?? "";
    for (const word of TECHNICAL) expect(text).not.toMatch(word);
  });

  it("platform mode: cancelling a new name keeps the active one and saves nothing", async () => {
    const calls = stub({ "GET /channel": () => json(200, channel({ email: PLATFORM_CONFIRMED })) });
    renderPage();
    const user = userEvent.setup();
    const name = (await screen.findByLabelText("Sender name")) as HTMLInputElement;
    await user.clear(name);
    await user.type(name, "Acme Group");
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("sender-preview")).toBeNull();
    expect((screen.getByLabelText("Sender name") as HTMLInputElement).value).toBe("Acme Boutique");
    expect(screen.getByRole("button", { name: "Save settings" })).toBeDisabled();
    expect(calls.some((c) => c.key === "PUT /channel")).toBe(false);
  });

  it("platform mode: until the name is confirmed the client is told what to do", async () => {
    stub({
      "GET /channel": () =>
        json(
          200,
          channel({
            email: PLATFORM_UNCONFIRMED,
            status: "action_required",
            status_message: "Confirm your sender name to finish setting up your email.",
          }),
        ),
    });
    renderPage();
    expect(await screen.findByText("Confirm your sender name to finish setting up your email.")).toBeInTheDocument();
    expect(screen.getByTestId("channel-status")).toHaveTextContent(/action|choose|needed/i);
  });
});

describe("ClientChannelPage: the daily sending limit", () => {
  const withSending = (sending: Record<string, unknown>) =>
    channel({ status: "ready", status_message: null, email: { ...channel().email, sending } });

  it.each([
    ["not_set", null, null, /limit is not set yet/],
    ["reached", 4, 4, /limit is reached/],
    ["unavailable", null, null, /cannot be checked/],
    ["ok", 2, 10, /Emails can go out today/],
  ])("explains the state %s", async (state, sent, limit, text) => {
    stub({ "GET /channel": () => json(200, withSending({ state, message: "x", daily_limit: limit, sent_today: sent })) });
    renderPage();
    expect(await screen.findByTestId("sending-state")).toHaveTextContent(text);
    for (const word of TECHNICAL) expect(document.body.textContent).not.toMatch(word);
  });

  it("shows nothing when the server does not say", async () => {
    stub({ "GET /channel": () => json(200, channel()) });
    renderPage();
    await screen.findByLabelText("Default subject");
    expect(screen.queryByTestId("sending-state")).not.toBeInTheDocument();
  });
});
