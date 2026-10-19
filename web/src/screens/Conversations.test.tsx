import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { Conversations } from "./Conversations";

const logout = vi.fn(async () => undefined);
vi.mock("../client/ClientAuthContext", () => ({ useClientAuth: () => ({ logout }) }));

const LIST = "GET /client/v1/prospects?limit=100&offset=0";

function row(id: string, name: string) {
  return {
    id,
    display_name: name,
    status: "active",
    stage: null,
    locale: null,
    lane_id: null,
    phone: null,
    email: null,
    preferred_channel: null,
    next_action_type: null,
    next_action_due_at: null,
    last_contacted_at: null,
    last_inbound_at: null,
    follow_up_count: 0,
  };
}

function prospects(rows: unknown[], total = rows.length) {
  return { total, limit: 100, offset: 0, prospects: rows };
}

function history(id: string, messages: unknown[]) {
  return { prospect_id: id, conversation_id: `conv-${id}`, origin: "unknown", messages };
}

function message(id: string, direction: "inbound" | "outbound", text: string, minute: number) {
  return { id, direction, channel: "whatsapp", text, timestamp: `2026-10-10T09:0${minute}:00Z` };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Handler = (init: RequestInit | undefined) => Response | Promise<Response>;

/** Only fetch is simulated (the server is not reachable from a unit test). */
function stub(routes: Record<string, Handler>) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input)}`;
      calls.push(key);
      const handler = routes[key];
      if (!handler) throw new Error(`unexpected fetch ${key}`);
      return handler(init);
    }),
  );
  return calls;
}

function renderPage(path: string) {
  render(
    <I18nProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/conversations" element={<Conversations />} />
          <Route path="/conversations/:id" element={<Conversations />} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  );
}

beforeEach(() => {
  // A wide screen: the list and the thread side by side.
  vi.stubGlobal(
    "matchMedia",
    (query: string) => ({ matches: true, media: query, addEventListener: () => undefined, removeEventListener: () => undefined }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  logout.mockClear();
});

describe("Conversations (live)", () => {
  it("lists the server's prospects and invites the client to pick one, without fetching a thread", async () => {
    const calls = stub({ [LIST]: () => json(200, prospects([row("p1", "Amel"), row("p2", "Bea")])) });
    renderPage("/conversations");
    expect(await screen.findByText("Amel")).toBeInTheDocument();
    expect(screen.getByText("Bea")).toBeInTheDocument();
    expect(screen.getByText("Choose a prospect to read the conversation.")).toBeInTheDocument();
    expect(calls).toEqual([LIST]);
  });

  it("shows the messages the server returns, oldest first, for the selected prospect", async () => {
    stub({
      [LIST]: () => json(200, prospects([row("p1", "Amel"), row("p2", "Bea")])),
      "GET /client/v1/conversations/p1": () =>
        json(200, history("p1", [message("m1", "outbound", "Hello Amel", 1), message("m2", "inbound", "Tell me more", 2)])),
    });
    renderPage("/conversations/p1");
    const first = await screen.findByTestId("message-m1");
    expect(first).toHaveTextContent("Hello Amel");
    expect(first).toHaveTextContent("Sent");
    const second = screen.getByTestId("message-m2");
    expect(second).toHaveTextContent("Tell me more");
    expect(second).toHaveTextContent("Received");
    expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Amel" })).toBeInTheDocument();
    // Read-only: nothing to write or approve with.
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("says so when nothing was exchanged yet", async () => {
    stub({
      [LIST]: () => json(200, prospects([row("p1", "Amel")])),
      "GET /client/v1/conversations/p1": () => json(200, history("p1", [])),
    });
    renderPage("/conversations/p1");
    expect(await screen.findByTestId("thread-empty")).toHaveTextContent("No message has been exchanged");
  });

  it("shows a not-found for a prospect that is not the client's, never someone else's messages", async () => {
    stub({
      [LIST]: () => json(200, prospects([row("p1", "Amel")])),
      "GET /client/v1/conversations/other": () => json(404, { detail: "no such prospect" }),
    });
    renderPage("/conversations/other");
    expect(await screen.findByRole("alert")).toHaveTextContent("does not exist");
    expect(screen.queryByTestId(/^message-/)).not.toBeInTheDocument();
  });

  it("shows the error and retries when the thread cannot be read", async () => {
    let reads = 0;
    stub({
      [LIST]: () => json(200, prospects([row("p1", "Amel")])),
      "GET /client/v1/conversations/p1": () =>
        reads++ === 0 ? json(503, { detail: "prospects are temporarily unavailable" }) : json(200, history("p1", [message("m1", "inbound", "Hi", 1)])),
    });
    const user = userEvent.setup();
    renderPage("/conversations/p1");
    expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("message-m1")).toBeInTheDocument();
  });

  it("points to Prospects when the list holds only the first prospects", async () => {
    stub({
      [LIST]: () => json(200, prospects([row("p1", "Amel")], 250)),
      "GET /client/v1/conversations/p1": () => json(200, history("p1", [])),
    });
    renderPage("/conversations/p1");
    const more = await screen.findByTestId("convo-list-more");
    expect(within(more).getByRole("link")).toHaveAttribute("href", "/prospects");
  });

  it("signs the client out when the session has ended", async () => {
    stub({ [LIST]: () => json(401, { detail: "not authenticated" }) });
    renderPage("/conversations");
    await waitFor(() => expect(logout).toHaveBeenCalled());
  });
});

describe("Conversations (history by prospect, channel and day)", () => {
  it("puts the prospects with the most recent exchange first and says channel and date", async () => {
    const recent = { ...row("p2", "Bea"), preferred_channel: "email", last_inbound_at: "2026-10-12T10:00:00Z" };
    const older = { ...row("p1", "Amel"), preferred_channel: "telegram", last_contacted_at: "2026-10-01T10:00:00Z" };
    stub({ [LIST]: () => json(200, prospects([older, recent, row("p3", "Zed")])) });
    renderPage("/conversations");
    await screen.findByText("Amel");
    const names = screen.getAllByRole("link").map((a) => a.textContent ?? "");
    expect(names[0]).toContain("Bea");
    expect(names[0]).toContain("email");
    expect(names[1]).toContain("Amel");
    expect(names[2]).toContain("Zed");
  });

  it("filters the list by channel", async () => {
    const a = { ...row("p1", "Amel"), preferred_channel: "telegram" };
    const b = { ...row("p2", "Bea"), preferred_channel: "email" };
    stub({ [LIST]: () => json(200, prospects([a, b])) });
    renderPage("/conversations");
    await screen.findByText("Amel");
    await userEvent.selectOptions(screen.getByLabelText("Channel"), "email");
    expect(screen.queryByText("Amel")).not.toBeInTheDocument();
    expect(screen.getByText("Bea")).toBeInTheDocument();
  });

  it("separates the messages by day", async () => {
    stub({
      [LIST]: () => json(200, prospects([row("p1", "Amel")])),
      "GET /client/v1/conversations/p1": () =>
        json(200, history("p1", [
          message("m1", "outbound", "Hello", 1),
          { id: "m2", direction: "inbound", channel: "email", text: "Hi", timestamp: "2026-10-11T09:00:00Z" },
        ])),
    });
    renderPage("/conversations/p1");
    await screen.findByTestId("message-m1");
    expect(screen.getByTestId("day-2026-10-10")).toBeInTheDocument();
    expect(screen.getByTestId("day-2026-10-11")).toBeInTheDocument();
  });
});
