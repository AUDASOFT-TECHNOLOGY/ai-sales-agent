import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { ClientChannelPage } from "./ClientChannelPage";

function channel(mode: "bot" | "account", over: Record<string, unknown> = {}) {
  return {
    version: 4,
    channel: "telegram",
    authorized: true,
    options: [{ key: "telegram", label: "Telegram" }],
    status: mode === "account" ? "action_required" : "waiting_platform",
    status_message: null,
    email: null,
    telegram: {
      mode,
      contact_prompt: "Share your number",
      contact_button: "Share",
      not_found_text: "Thanks",
      consent_field: "telegram_consent",
      max_new_conversations_per_day: 10,
    },
    sender_name_proposal: null,
    updated_at: null,
    updated_by: null,
    ...over,
  };
}

function account(over: Record<string, unknown> = {}) {
  return {
    available: true,
    state: "not_connected",
    limited: false,
    login: null,
    new_today: 0,
    daily_limit: 10,
    ...over,
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Handler = (init: RequestInit | undefined) => Response | Promise<Response>;

function stub(routes: Record<string, Handler>) {
  const calls: Array<{ key: string; body: string | undefined }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input).replace("/client/v1", "")}`;
      calls.push({ key, body: init?.body as string | undefined });
      const handler = routes[key];
      if (!handler) throw new Error(`unexpected fetch ${key}`);
      return handler(init);
    }),
  );
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

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("Telegram from the client's own account", () => {
  it("lets the client choose its own account instead of the bot", async () => {
    const calls = stub({
      "GET /channel": () => json(200, channel("bot")),
      "PUT /channel": () => json(200, channel("account")),
      "GET /channel/telegram/account": () => json(200, account()),
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Use my Telegram account" }));
    await waitFor(() => expect(screen.getByTestId("tgaccount-state")).toHaveTextContent("Not connected"));
    const put = calls.find((c) => c.key === "PUT /channel")!;
    expect(JSON.parse(put.body!)).toEqual({
      expected_version: 4,
      channel: "telegram",
      telegram: { use_account: true },
    });
  });

  it("signs in with the phone, the code and the two-step password, and shows none of them back", async () => {
    let step: "none" | "code" | "password" | "done" = "none";
    const calls = stub({
      "GET /channel": () => json(200, channel("account")),
      "GET /channel/telegram/account": () =>
        json(200, account({ state: step === "done" ? "connected" : "not_connected", login: step === "code" || step === "password" ? step : null })),
      "POST /channel/telegram/account/start": () => {
        step = "code";
        return json(200, account({ login: "code" }));
      },
      "POST /channel/telegram/account/code": () => {
        step = "password";
        return json(200, account({ login: "password" }));
      },
      "POST /channel/telegram/account/password": () => {
        step = "done";
        return json(200, account({ state: "connected" }));
      },
      "GET /channel/telegram/waiting": () => json(200, { consent_field: "telegram_consent", waiting: 0, people: [] }),
    });
    renderPage();
    await userEvent.type(await screen.findByLabelText("Your Telegram phone number"), "+216 20 123 456");
    await userEvent.click(screen.getByRole("button", { name: "Send me the code" }));
    await userEvent.type(await screen.findByLabelText("The code Telegram sent you"), "12345");
    await userEvent.click(screen.getByRole("button", { name: "Confirm the code" }));
    const password = await screen.findByLabelText("Your Telegram two-step password");
    expect(password).toHaveAttribute("type", "password");
    await userEvent.type(password, "s3cret-two-step");
    await userEvent.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(screen.getByTestId("tgaccount-state")).toHaveTextContent("Connected"));
    expect(document.body.textContent).not.toContain("s3cret-two-step");
    expect(document.body.textContent).not.toContain("12345");
    expect(document.body.textContent).not.toContain("20 123 456");
    expect(calls.some((c) => c.key === "POST /channel/telegram/account/password")).toBe(true);
  });

  it("says plainly what went wrong without repeating what was typed", async () => {
    stub({
      "GET /channel": () => json(200, channel("account")),
      "GET /channel/telegram/account": () => json(200, account()),
      "POST /channel/telegram/account/start": () => json(422, { detail: "this is not a phone number Telegram can use" }),
    });
    renderPage();
    await userEvent.type(await screen.findByLabelText("Your Telegram phone number"), "not-a-number");
    await userEvent.click(screen.getByRole("button", { name: "Send me the code" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This phone number cannot be used");
    expect(screen.getByRole("alert")).not.toHaveTextContent("not-a-number");
  });

  it("says when Telegram limited the account and keeps replies going", async () => {
    stub({
      "GET /channel": () => json(200, channel("account", { status: "ready", status_message: "limited" })),
      "GET /channel/telegram/account": () => json(200, account({ state: "connected", limited: true, new_today: 3 })),
      "GET /channel/telegram/waiting": () => json(200, { consent_field: "telegram_consent", waiting: 0, people: [] }),
    });
    renderPage();
    expect(await screen.findByTestId("tgaccount-limited")).toHaveTextContent("replies to people who write to you go on");
    expect(screen.getByTestId("tgaccount-today")).toHaveTextContent(/3\W+of\W+10/);
  });

  it("asks for a new sign-in when Telegram ended the connection", async () => {
    stub({
      "GET /channel": () => json(200, channel("account")),
      "GET /channel/telegram/account": () => json(200, account({ state: "reauth_required" })),
    });
    renderPage();
    expect(await screen.findByText("Telegram ended the connection. Sign in again to let Revansa write.")).toBeInTheDocument();
    expect(screen.getByLabelText("Your Telegram phone number")).toBeInTheDocument();
  });

  it("lists who Revansa is not writing to yet and why", async () => {
    stub({
      "GET /channel": () => json(200, channel("account", { status: "ready" })),
      "GET /channel/telegram/account": () => json(200, account({ state: "connected" })),
      "GET /channel/telegram/waiting": () =>
        json(200, {
          consent_field: "telegram_consent",
          waiting: 2,
          people: [
            { prospect_id: "a", display_name: "Lina", phone: "+1", reason: "needs_consent" },
            { prospect_id: "b", display_name: "Omar", phone: null, reason: "needs_phone" },
          ],
        }),
    });
    renderPage();
    const list = await screen.findByTestId("tgaccount-waiting");
    expect(list).toHaveTextContent("Lina");
    expect(list).toHaveTextContent("not marked as expecting your message");
    expect(list).toHaveTextContent("no phone number to find them with");
  });

  it("disconnects after a confirmation and says when Telegram could not be told", async () => {
    let connected = true;
    stub({
      "GET /channel": () => json(200, channel("account", { status: "ready" })),
      "GET /channel/telegram/account": () => json(200, account({ state: connected ? "connected" : "not_connected" })),
      "GET /channel/telegram/waiting": () => json(200, { consent_field: "telegram_consent", waiting: 0, people: [] }),
      "DELETE /channel/telegram/account": () => {
        connected = false;
        return json(200, { disconnected: true, ended_at_telegram: false });
      },
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Disconnect" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Disconnect" })[0]!);
    expect(await screen.findByText(/could not tell Telegram/)).toBeInTheDocument();
  });

  it("saves the column and the daily maximum", async () => {
    const calls = stub({
      "GET /channel": () => json(200, channel("account")),
      "GET /channel/telegram/account": () => json(200, account()),
      "PUT /channel": () => json(200, channel("account")),
    });
    renderPage();
    const field = await screen.findByLabelText("Column that says who expects your message");
    await userEvent.clear(field);
    await userEvent.type(field, "opt_in");
    await userEvent.click(screen.getAllByRole("button", { name: "Save settings" }).at(-1)!);
    await waitFor(() => expect(calls.some((c) => c.key === "PUT /channel")).toBe(true));
    const body = JSON.parse(calls.find((c) => c.key === "PUT /channel")!.body!);
    expect(body.telegram).toEqual({ consent_field: "opt_in", max_new_conversations_per_day: 10 });
  });

  it("keeps the bot's wording form only for the bot", async () => {
    stub({ "GET /channel": () => json(200, channel("bot")), "GET /channel/invitations": () => json(404, { detail: "x" }) });
    renderPage();
    expect(await screen.findByLabelText("Message asking for the phone number")).toBeInTheDocument();
  });
});
