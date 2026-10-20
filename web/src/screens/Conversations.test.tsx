import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { Conversations } from "./Conversations";

const logout = vi.fn(async () => undefined);
vi.mock("../client/ClientAuthContext", () => ({ useClientAuth: () => ({ logout }) }));

const LIST = "GET /client/v1/conversations?limit=25&offset=0&sort=recent";
const THREAD = (id: string, offset = 0) => `GET /client/v1/conversations/${id}?limit=50&offset=${offset}&order=desc`;

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

function prospects(rows: unknown[], total = rows.length, offset = 0) {
  return {
    total,
    limit: 25,
    offset,
    conversations: rows.map((p) => ({ prospect: p, message_count: 3, last_message_at: (p as { last_inbound_at: string | null }).last_inbound_at, last_message_direction: "inbound" })),
  };
}

function history(id: string, messages: unknown[], total = messages.length) {
  // The server answers newest first.
  return { prospect_id: id, conversation_id: `conv-${id}`, origin: "unknown", messages: [...messages].reverse(), total_messages: total, limit: 50, offset: 0, order: "desc", errors: [] };
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
    expect(calls).toContain(LIST);
    expect(calls.filter((c) => /conversations\/[^?]/.test(c))).toEqual([]); // no thread fetched
  });

  it("shows the messages the server returns, oldest first, for the selected prospect", async () => {
    stub({
      [LIST]: () => json(200, prospects([row("p1", "Amel"), row("p2", "Bea")])),
      [THREAD("p1")]: () =>
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
      [THREAD("p1")]: () => json(200, history("p1", [])),
    });
    renderPage("/conversations/p1");
    expect(await screen.findByTestId("thread-empty")).toHaveTextContent("No message has been exchanged");
  });

  it("shows a not-found for a prospect that is not the client's, never someone else's messages", async () => {
    stub({
      [LIST]: () => json(200, prospects([row("p1", "Amel")])),
      [THREAD("other")]: () => json(404, { detail: "no such prospect" }),
    });
    renderPage("/conversations/other");
    expect(await screen.findByRole("alert")).toHaveTextContent("does not exist");
    expect(screen.queryByTestId(/^message-/)).not.toBeInTheDocument();
  });

  it("shows the error and retries when the thread cannot be read", async () => {
    let reads = 0;
    stub({
      [LIST]: () => json(200, prospects([row("p1", "Amel")])),
      [THREAD("p1")]: () =>
        reads++ === 0 ? json(503, { detail: "prospects are temporarily unavailable" }) : json(200, history("p1", [message("m1", "inbound", "Hi", 1)])),
    });
    const user = userEvent.setup();
    renderPage("/conversations/p1");
    expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("message-m1")).toBeInTheDocument();
  });

  it("signs the client out when the session has ended", async () => {
    stub({ [LIST]: () => json(401, { detail: "not authenticated" }) });
    renderPage("/conversations");
    await waitFor(() => expect(logout).toHaveBeenCalled());
  });
});

describe("Conversations (search, filters, pages)", () => {
  it("shows the last exchange, the channel and the message count of each conversation", async () => {
    const recent = { ...row("p2", "Bea"), preferred_channel: "email", last_inbound_at: "2026-10-12T10:00:00Z" };
    stub({ [LIST]: () => json(200, prospects([recent, row("p1", "Amel")])) });
    renderPage("/conversations");
    await screen.findByText("Amel");
    const links = screen.getAllByRole("link").map((a) => a.textContent ?? "");
    expect(links[0]).toContain("Bea");
    expect(links[0]).toContain("email");
    expect(links[0]).toContain("3 messages");
  });

  it("asks the server to filter by channel and to search, and shows what it returns", async () => {
    const calls = stub({
      [LIST]: () => json(200, prospects([row("p1", "Amel"), row("p2", "Bea")])),
      "GET /client/v1/conversations?limit=25&offset=0&channel=email&sort=recent": () => json(200, prospects([row("p2", "Bea")])),
      "GET /client/v1/conversations?limit=25&offset=0&q=bea&channel=email&sort=recent": () => json(200, prospects([row("p2", "Bea")])),
    });
    const user = userEvent.setup();
    renderPage("/conversations");
    await screen.findByText("Amel");
    await user.selectOptions(screen.getByLabelText("Channel"), "email");
    await waitFor(() => expect(screen.queryByText("Amel")).not.toBeInTheDocument());
    expect(screen.getByText("Bea")).toBeInTheDocument();
    await user.type(screen.getByRole("searchbox"), "bea");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(calls).toContain("GET /client/v1/conversations?limit=25&offset=0&q=bea&channel=email&sort=recent"));
  });

  it("says so when nothing matches the filters", async () => {
    stub({
      [LIST]: () => json(200, prospects([row("p1", "Amel")])),
      "GET /client/v1/conversations?limit=25&offset=0&status=closed&sort=recent": () => json(200, prospects([])),
    });
    const user = userEvent.setup();
    renderPage("/conversations");
    await screen.findByText("Amel");
    await user.selectOptions(screen.getByLabelText("Status"), "closed");
    expect(await screen.findByTestId("convo-list-empty")).toHaveTextContent("No conversation matches");
  });

  it("pages the list on the server", async () => {
    const calls = stub({
      [LIST]: () => json(200, prospects([row("p1", "Amel")], 30)),
      "GET /client/v1/conversations?limit=25&offset=25&sort=recent": () => json(200, prospects([row("p9", "Zed")], 30, 25)),
    });
    const user = userEvent.setup();
    renderPage("/conversations");
    await screen.findByText("Amel");
    expect(screen.getByTestId("convo-list-page")).toHaveTextContent(/1\D+2/);
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Zed")).toBeInTheDocument();
    expect(calls).toContain("GET /client/v1/conversations?limit=25&offset=25&sort=recent");
  });
});

describe("Conversations (thread)", () => {
  it("separates the messages by day", async () => {
    stub({
      [LIST]: () => json(200, prospects([row("p1", "Amel")])),
      [THREAD("p1")]: () =>
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

  it("opens on the latest messages and loads older ones on request", async () => {
    const newest = Array.from({ length: 50 }, (_, i) => message(`n${i}`, "inbound", `new ${i}`, 5));
    stub({
      [LIST]: () => json(200, prospects([row("p1", "Amel")])),
      [THREAD("p1")]: () => json(200, history("p1", newest, 51)),
      [THREAD("p1", 50)]: () => json(200, { ...history("p1", [message("old", "outbound", "the very first", 1)], 51), offset: 50 }),
    });
    const user = userEvent.setup();
    renderPage("/conversations/p1");
    await screen.findByTestId("message-n0");
    expect(screen.getByTestId("thread-count")).toHaveTextContent("50 of 51");
    await user.click(screen.getByRole("button", { name: "Show older messages" }));
    const old = await screen.findByTestId("message-old");
    expect(screen.getByTestId("message-n0").compareDocumentPosition(old) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Show older messages" })).not.toBeInTheDocument();
  });

  it("shows the status, the next follow-up and the actions that failed", async () => {
    stub({
      [LIST]: () => json(200, prospects([row("p1", "Amel")])),
      [THREAD("p1")]: () =>
        json(200, {
          ...history("p1", [message("m1", "outbound", "Hello", 1)]),
          prospect: { ...row("p1", "Amel"), status: "paused", needs_human: true, preferred_channel: "email" },
          next_follow_up: { action_type: "follow_up", due_at: "2026-10-20T09:00:00Z" },
          errors: [{ action_id: "a1", action_type: "follow_up", due_at: "2026-10-09T09:00:00Z", attempts: 2, reason: "channel refused" }],
        }),
    });
    renderPage("/conversations/p1");
    await screen.findByTestId("message-m1");
    expect(screen.getByTestId("thread-status")).toHaveTextContent("Waiting for a person");
    expect(screen.getByTestId("thread-follow-up")).toHaveTextContent("Next follow-up");
    expect(screen.getByTestId("thread-errors")).toHaveTextContent("channel refused");
  });
});
