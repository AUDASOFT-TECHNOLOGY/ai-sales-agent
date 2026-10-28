import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../../i18n";
import { ClientAuthProvider } from "../ClientAuthContext";
import { ConnectionsPage } from "./ConnectionsPage";
import { summaryOf, viewsOf, type ConnectionData } from "./connectionModel";
import { channelChoices, sourcePending } from "./useSelectable";

const CHANNEL = {
  version: 3,
  channel: "email",
  authorized: true,
  options: [
    { key: "email", label: "Email" },
    { key: "whatsapp", label: "WhatsApp" },
    { key: "telegram", label: "Telegram" },
  ],
  status: "waiting_platform",
  status_message: null,
  email: { mode: "gmail", from_name: null, sender_name_confirmed: false, subject: null, activation: null, sending: null },
  telegram: null,
  sender_name_proposal: null,
  updated_at: null,
  updated_by: null,
};

const GMAIL = { state: "not_connected", address: null, send_cap_configured: true, send_ready: false, message: "", mailbox_sync: "not_started", last_sync_at: null };
const WHATSAPP = { state: "not_connected", number: "+21620000000", verified_name: null, status: "waiting_platform", message: null, can_start_conversations: false, delivered_7d: 0, failed_7d: 0 };
const TELEGRAM = { available: true, state: "not_connected", limited: false, login: null, new_today: null, daily_limit: null };
const HUBSPOT = { state: "not_connected", account: null, message: "" };
const SHEETS = { state: "connected", address: "ventes@naga.tn", spreadsheet_saved: true, message: "" };
const SOURCE = { version: 1, source: "google_sheets", options: [{ key: "google_sheets", label: "Google Sheets" }, { key: "hubspot", label: "HubSpot" }], google_sheets: null };
const NO_HINTS = { gmail: null, sheets: null, hubspot: null, telegram: null };
const INFO = { hints: NO_HINTS, selectable: { channels: { email: true, whatsapp: true, telegram: true }, sources: { google_sheets: true, hubspot: true } } };

const ROUTES: Record<string, unknown> = {
  "/channel": CHANNEL,
  "/channel/google": GMAIL,
  "/channel/whatsapp": WHATSAPP,
  "/channel/telegram/account": TELEGRAM,
  "/hubspot-access": HUBSPOT,
  "/sheets-access": SHEETS,
  "/source": SOURCE,
  "/connections": INFO,
  "/auth/me": { client_id: "naga", display_name: "Naga", whatsapp_number: "+21620000000" },
};

type Call = { method: string; path: string; body: unknown };

function stub(over: Record<string, unknown> = {}) {
  const routes = { ...ROUTES, ...over };
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^.*\/client\/v1/, "").split("?")[0]!;
      calls.push({ method: init?.method ?? "GET", path, body: init?.body ? JSON.parse(String(init.body)) : null });
      if (!(path in routes)) return new Response("{}", { status: 404, headers: { "Content-Type": "application/json" } });
      return new Response(JSON.stringify(routes[path]), { status: 200, headers: { "Content-Type": "application/json" } });
    }),
  );
  return calls;
}

function renderPage() {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <ClientAuthProvider>
          <ConnectionsPage />
        </ClientAuthProvider>
      </MemoryRouter>
    </I18nProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

// The platform's application is not configured: every provider answers "unavailable".
const NOT_READY = {
  "/channel/google": { ...GMAIL, state: "unavailable" },
  "/channel/whatsapp": { ...WHATSAPP, state: "unavailable" },
  "/channel/telegram/account": { ...TELEGRAM, available: false },
  "/hubspot-access": { ...HUBSPOT, state: "unavailable" },
  "/sheets-access": { ...SHEETS, state: "unavailable", address: null },
};

describe("ConnectionsPage", () => {
  it("shows one row per connection, connected or disconnected, with the service's own button", async () => {
    stub();
    renderPage();
    for (const id of ["email", "whatsapp", "telegram", "hubspot", "sheets"]) {
      expect(await screen.findByTestId(`connection-${id}`)).toBeInTheDocument();
    }
    expect(within(screen.getByTestId("connection-sheets")).getByText("Connected")).toBeInTheDocument();
    const email = screen.getByTestId("connection-email");
    expect(within(email).getByText("Disconnected")).toBeInTheDocument();
    expect(within(email).getByRole("button", { name: "Connect my Gmail" })).toBeInTheDocument();
    expect(within(screen.getByTestId("connection-hubspot")).getByRole("button", { name: "Connect my HubSpot" })).toBeInTheDocument();
    expect(within(screen.getByTestId("connection-whatsapp")).getByRole("button", { name: "Connect my WhatsApp Business" })).toBeInTheDocument();
    expect(within(screen.getByTestId("connection-telegram")).getByRole("button", { name: "Connect my Telegram" })).toBeInTheDocument();
  });

  it("counts what is connected and names the next step", async () => {
    stub();
    renderPage();
    const summary = await screen.findByTestId("connections-summary");
    // Numbers are wrapped in direction marks by the translator: compare the words.
    const words = (summary.textContent ?? "").replace(/[⁦⁩]/g, "");
    expect(words).toContain("1 of 5 connected");
    expect(words).toContain("Next step: connect Gmail");
  });

  it("does not offer a source Revansa does not give this account", async () => {
    stub({ "/source": { ...SOURCE, options: [{ key: "google_sheets", label: "Google Sheets" }] } });
    renderPage();
    await screen.findByTestId("connection-email");
    expect(screen.queryByTestId("connection-hubspot")).toBeNull();
  });

  it("goes to the provider's page when the client connects its Gmail", async () => {
    const calls = stub({ "/channel/google/start": { authorize_url: "https://accounts.google.com/x" }, "/connections/hints/gmail": INFO });
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign, search: "", pathname: "/", hash: "" });
    renderPage();
    const row = await screen.findByTestId("connection-email");
    await userEvent.type(within(row).getByLabelText(/Gmail address to connect/), "sales@naga.tn");
    await userEvent.click(within(row).getByRole("button", { name: "Connect my Gmail" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://accounts.google.com/x"));
    // The address is kept before the client leaves.
    expect(calls.some((c) => c.method === "PUT" && c.path === "/connections/hints/gmail" && (c.body as { value: string }).value === "sales@naga.tn")).toBe(true);
  });
});

describe("whatever Revansa has or has not configured", () => {
  it("never says anything about Revansa's side on a connection", async () => {
    stub(NOT_READY);
    renderPage();
    for (const id of ["email", "whatsapp", "telegram", "hubspot", "sheets"]) {
      const row = await screen.findByTestId(`connection-${id}`);
      expect(row).toHaveAttribute("data-standing", "todo");
      expect(within(row).getByText("Disconnected")).toBeInTheDocument();
      expect(row).not.toHaveTextContent(/Revansa is finishing|Not available|unavailable/i);
      expect(within(row).getByRole("button", { name: /^Connect my / })).toBeEnabled();
    }
  });

  it("keeps what the client gives and tells it the request is saved", async () => {
    const calls = stub({ ...NOT_READY, "/connections/hints/gmail": INFO });
    renderPage();
    const row = await screen.findByTestId("connection-email");
    await userEvent.type(within(row).getByLabelText(/Gmail address to connect/), "sales@naga.tn");
    await userEvent.click(within(row).getByRole("button", { name: "Connect my Gmail" }));
    expect(await within(row).findByText(/Your request is saved/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PUT" && c.path === "/connections/hints/gmail")).toBe(true);
    expect(calls.some((c) => c.path === "/channel/google/start")).toBe(false); // nothing to authorise yet
  });

  it("asks for the account first when there is nothing to keep", async () => {
    stub(NOT_READY);
    renderPage();
    const row = await screen.findByTestId("connection-email");
    await userEvent.click(within(row).getByRole("button", { name: "Connect my Gmail" }));
    expect(await within(row).findByText("Enter the account to connect first.")).toBeInTheDocument();
  });

  it("remembers the account the client gave and shows what is left to do", async () => {
    stub({ ...NOT_READY, "/connections": { ...INFO, hints: { ...NO_HINTS, gmail: "sales@naga.tn" } } });
    renderPage();
    const row = await screen.findByTestId("connection-email");
    await waitFor(() => expect(within(row).getByLabelText(/Gmail address to connect/)).toHaveValue("sales@naga.tn"));
    expect(row).toHaveTextContent("Account sales@naga.tn saved");
  });

  it("lets the client choose its WhatsApp number, even without a number yet", async () => {
    stub({ ...NOT_READY, "/channel/whatsapp": { ...NOT_READY["/channel/whatsapp"], number: null }, "/auth/me": { client_id: "naga", display_name: "Naga", whatsapp_number: null } });
    renderPage();
    const row = await screen.findByTestId("connection-whatsapp");
    expect(await within(row).findByLabelText(/Your company's WhatsApp number/)).toBeInTheDocument();
    await userEvent.click(within(row).getByRole("button", { name: "Connect my WhatsApp Business" }));
    expect(await within(row).findByText("Enter the account to connect first.")).toBeInTheDocument();
  });
});

describe("Telegram sign-in", () => {
  it("asks for the phone number, then for the code Telegram sent", async () => {
    stub({ "/channel/telegram/account/start": TELEGRAM, "/connections/hints/telegram": INFO });
    // Telegram sent the code: from now on the server says a sign-in waits for it.
    const base = vi.mocked(fetch);
    let sent = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).endsWith("/channel/telegram/account/start")) sent = true;
        if (String(input).endsWith("/channel/telegram/account") && sent) {
          return new Response(JSON.stringify({ ...TELEGRAM, login: "code" }), { status: 200, headers: { "Content-Type": "application/json" } });
        }
        return base(input, init);
      }),
    );
    renderPage();
    const row = await screen.findByTestId("connection-telegram");
    await userEvent.type(within(row).getByLabelText(/Your Telegram phone number/), "+216 20 123 456");
    await userEvent.click(within(row).getByRole("button", { name: "Connect my Telegram" }));
    expect(await within(row).findByLabelText("The code Telegram sent you")).toBeInTheDocument();
  });
});

describe("Telegram's rule", () => {
  it("tells the client why the agent starts at most 5 conversations a day", async () => {
    stub();
    renderPage();
    const row = await screen.findByTestId("connection-telegram");
    expect(within(row).getByText("Telegram forbids unsolicited messages")).toBeInTheDocument();
    expect(row).toHaveTextContent(/at most 5 new conversations a day/);
    expect(row).toHaveTextContent(/writes each first message specially for the person/);
  });
});

describe("what a circuit can choose", () => {
  const name = (key: string) => key;
  it("shows every channel, greys the ones that are not usable yet and never greys the one in use", () => {
    const selectable = { channels: { email: true, whatsapp: false, telegram: false }, sources: {} };
    const choices = channelChoices([{ key: "email", label: "Email" }, { key: "whatsapp", label: "WhatsApp" }], selectable, "whatsapp", name);
    expect(choices.map((c) => [c.key, c.pending])).toEqual([["email", false], ["whatsapp", false], ["telegram", true]]);
  });

  it("greys a source that is not usable yet", () => {
    const selectable = { channels: {}, sources: { google_sheets: true, hubspot: false } };
    expect(sourcePending(selectable, "hubspot", "google_sheets")).toBe(true);
    expect(sourcePending(selectable, "hubspot", "hubspot")).toBe(false);
    expect(sourcePending(null, "hubspot", null)).toBe(false); // unknown: nothing is greyed on a guess
  });
});

describe("connection standings", () => {
  const base: ConnectionData = { channel: null, gmail: null, whatsapp: null, telegram: null, hubspot: null, sheets: null, hints: null, offered: ["google_sheets", "hubspot"], shareWith: null, number: null };

  it("is connected only when the provider accepted the client's authorisation", () => {
    const [email] = viewsOf({ ...base, gmail: { ...GMAIL, state: "connected", address: "a@b.c", send_cap_configured: false } as ConnectionData["gmail"] });
    expect(email).toMatchObject({ id: "email", standing: "connected" });
  });

  it("is only ever connected or not, whatever Revansa has configured", () => {
    const views = viewsOf({ ...base, channel: { ...CHANNEL, options: [{ key: "email", label: "Email" }] } as ConnectionData["channel"], gmail: { ...GMAIL, state: "unavailable" } as ConnectionData["gmail"] });
    expect(new Set(views.map((v) => v.standing))).toEqual(new Set(["todo"]));
    expect(views.find((v) => v.id === "whatsapp")?.pending).toBe(true); // never shown, only decides what the button does
    expect(summaryOf(views).total).toBe(5);
  });

  it("reads a Gmail to confirm again as a reconnection", () => {
    const [email] = viewsOf({ ...base, gmail: { ...GMAIL, state: "reauth_required", address: "a@b.c" } as ConnectionData["gmail"] });
    expect(email).toMatchObject({ standing: "todo", reconnect: true });
  });
});
