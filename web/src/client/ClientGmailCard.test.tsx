import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { ClientChannelPage } from "./ClientChannelPage";
import { googleReturnOf } from "./ClientGmailCard";

function channel(mode: "platform" | "gmail" | "custom", over: Record<string, unknown> = {}) {
  return {
    version: 3,
    channel: "email",
    authorized: true,
    options: [{ key: "email", label: "Email" }],
    status: "action_required",
    status_message: "Connect your Gmail so Revansa can write to your prospects.",
    email: { mode, from_name: "Acme", sender_name_confirmed: mode === "platform", subject: "Hello" },
    telegram: null,
    sender_name_proposal: "Acme Shop",
    updated_at: null,
    updated_by: null,
    ...over,
  };
}

function gmail(over: Record<string, unknown> = {}) {
  return {
    state: "not_connected",
    address: null,
    send_cap_configured: false,
    send_ready: false,
    message: "Connect your Gmail so Revansa can write to your prospects.",
    mailbox_sync: "not_started",
    last_sync_at: null,
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
  window.history.replaceState(null, "", "/");
});

describe("Gmail mailbox on the channel page", () => {
  it("reads the Google return and nothing else", () => {
    expect(googleReturnOf("?google=connected")).toBe("connected");
    expect(googleReturnOf("?google=scope")).toBe("scope");
    expect(googleReturnOf("?google=<script>")).toBeNull();
    expect(googleReturnOf("")).toBeNull();
  });

  it("lets the client choose Gmail instead of a Revansa mailbox, with the exclusive choice in the request", async () => {
    const calls = stub({
      "GET /channel": () => json(200, channel("platform")),
      "PUT /channel": () => json(200, channel("gmail")),
      "GET /channel/google": () => json(200, gmail()),
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Use my Gmail" }));
    expect(await screen.findByRole("button", { name: "Connect Revansa with my Gmail" })).toBeInTheDocument();
    const put = calls.find((c) => c.key === "PUT /channel");
    expect(JSON.parse(put?.body ?? "{}")).toEqual({ expected_version: 3, channel: "email", email: { use_gmail: true } });
    // The only way back is through the mailbox Revansa provides; nothing asks for a password.
    expect(document.querySelectorAll('input[type="password"]')).toHaveLength(0);
  });

  it("starts the Google consent by redirecting to the URL the server gives, typing nothing", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign, search: "", pathname: "/" });
    const calls = stub({
      "GET /channel": () => json(200, channel("gmail")),
      "GET /channel/google": () => json(200, gmail()),
      "POST /channel/google/start": () => json(200, { authorize_url: "https://accounts.google.com/o/oauth2/v2/auth?state=s" }),
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Connect Revansa with my Gmail" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://accounts.google.com/o/oauth2/v2/auth?state=s"));
    expect(calls.find((c) => c.key === "POST /channel/google/start")?.body).toBeUndefined();
  });

  it("shows the connected address, the sync state and the missing-cap warning, with reconnect and disconnect", async () => {
    stub({
      "GET /channel": () => json(200, channel("gmail", { status: "waiting_platform" })),
      "GET /channel/google": () =>
        json(200, gmail({ state: "connected", address: "sales@gmail.com", mailbox_sync: "ok", last_sync_at: "2026-03-01T09:00:00Z" })),
    });
    renderPage();
    expect(await screen.findByText(/sales@gmail\.com/)).toBeInTheDocument();
    expect(screen.getByTestId("gmail-cap-warning")).toHaveTextContent(/not set yet/);
    expect(screen.getByTestId("gmail-sync")).toHaveTextContent(/Replies are being read/);
    expect(screen.getByRole("button", { name: "Reconnect my Gmail" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeInTheDocument();
  });

  it("asks to reconnect when Google needs a new authorisation", async () => {
    stub({
      "GET /channel": () => json(200, channel("gmail")),
      "GET /channel/google": () =>
        json(200, gmail({ state: "reauth_required", address: "sales@gmail.com", message: "Google needs you to authorise Revansa again. Reconnect your Gmail." })),
    });
    renderPage();
    expect(await screen.findByText(/Reconnection needed/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reconnect my Gmail" })).toBeInTheDocument();
  });

  it("disconnects only after a confirmation", async () => {
    const calls = stub({
      "GET /channel": () => json(200, channel("gmail")),
      "GET /channel/google": () => json(200, gmail({ state: "connected", address: "sales@gmail.com", send_cap_configured: true, send_ready: true })),
      "POST /channel/google/disconnect": () => json(200, gmail()),
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Disconnect" }));
    expect(calls.some((c) => c.key === "POST /channel/google/disconnect")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Yes, disconnect" }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /channel/google/disconnect")).toBe(true));
    expect(await screen.findByText("Not connected")).toBeInTheDocument();
  });

  it("explains the return from Google and cleans the address bar", async () => {
    window.history.replaceState(null, "", "/client/channel?google=scope");
    stub({
      "GET /channel": () => json(200, channel("gmail")),
      "GET /channel/google": () => json(200, gmail()),
    });
    renderPage();
    expect(await screen.findByTestId("gmail-return")).toHaveTextContent(/full mail permission/);
    expect(window.location.search).toBe("");
  });

  it("says Gmail is not available yet when the platform has not set Google up", async () => {
    stub({
      "GET /channel": () => json(200, channel("gmail", { status: "waiting_platform" })),
      "GET /channel/google": () => json(200, gmail({ state: "unavailable", message: "Gmail is not available yet. Revansa has to finish setting it up." })),
    });
    renderPage();
    expect(await screen.findAllByText(/not available yet/)).not.toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Connect Revansa with my Gmail" })).not.toBeInTheDocument();
  });

  it("shows a refusal of the switch without technical words", async () => {
    stub({
      "GET /channel": () => json(200, channel("platform")),
      "PUT /channel": () => json(409, { detail: "a Revansa mailbox is already set up for this account: it cannot also use Gmail" }),
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Use my Gmail" }));
    expect(await screen.findByText("This cannot be done right now.")).toBeInTheDocument();
  });

  it("does not offer Gmail on a mailbox the Admin set up", async () => {
    stub({ "GET /channel": () => json(200, channel("custom")) });
    renderPage();
    await screen.findByLabelText("Default subject");
    expect(screen.queryByRole("button", { name: "Use my Gmail" })).not.toBeInTheDocument();
  });
});
