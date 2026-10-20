import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { Conversations } from "./Conversations";

const logout = vi.fn(async () => undefined);
vi.mock("../client/ClientAuthContext", () => ({ useClientAuth: () => ({ logout }) }));

const LIST = "GET /client/v1/conversations?limit=25&offset=0&sort=recent";
const THREAD = "GET /client/v1/conversations/p1?limit=50&offset=0&order=desc";
const TAKE = "POST /client/v1/conversations/p1/take-over";
const RELEASE = "POST /client/v1/conversations/p1/release";
const SEND = "POST /client/v1/conversations/p1/messages";

type Control = { mode: "agent" | "human"; awaiting_human: boolean; taken_by: string | null; taken_at: string | null; reason: string | null; released_at: string | null };
const AGENT: Control = { mode: "agent", awaiting_human: false, taken_by: null, taken_at: null, reason: null, released_at: null };
const WAITING: Control = { mode: "human", awaiting_human: true, taken_by: null, taken_at: "2026-10-10T09:00:00Z", reason: "asks for a discount", released_at: null };
const TAKEN: Control = { mode: "human", awaiting_human: false, taken_by: "client:alpha", taken_at: "2026-10-10T09:00:00Z", reason: null, released_at: null };

function prospect(status = "active", extra: Record<string, unknown> = {}) {
  return {
    id: "p1",
    display_name: "Amel",
    status,
    stage: null,
    locale: null,
    lane_id: null,
    phone: null,
    email: null,
    preferred_channel: "whatsapp",
    next_action_type: null,
    next_action_due_at: null,
    last_contacted_at: null,
    last_inbound_at: null,
    follow_up_count: 0,
    needs_human: status === "paused",
    ...extra,
  };
}

function list(needsHuman = false) {
  return {
    total: 1,
    limit: 25,
    offset: 0,
    conversations: [{ prospect: prospect(needsHuman ? "paused" : "active"), message_count: 1, last_message_at: null, last_message_direction: "inbound" }],
  };
}

function thread(control: Control | null, opts: { status?: string; messages?: unknown[]; nextFollowUp?: unknown } = {}) {
  const messages = opts.messages ?? [{ id: "m1", direction: "inbound", channel: "whatsapp", text: "Can I get a discount?", timestamp: "2026-10-10T09:01:00Z", author: "prospect" }];
  return {
    prospect_id: "p1",
    conversation_id: "conv-p1",
    origin: "unknown",
    messages: [...messages].reverse(),
    total_messages: messages.length,
    limit: 50,
    offset: 0,
    order: "desc",
    errors: [],
    prospect: prospect(opts.status ?? "active"),
    next_follow_up: opts.nextFollowUp ?? null,
    control,
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Handler = (init: RequestInit | undefined) => Response | Promise<Response>;

/** Only fetch is simulated. A route may be given several answers: they are used in order, the last one repeating. */
function stub(routes: Record<string, Handler | Handler[]>) {
  const calls: Array<{ key: string; body: unknown }> = [];
  const used: Record<string, number> = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input)}`;
      calls.push({ key, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const found = routes[key];
      if (!found) throw new Error(`unexpected fetch ${key}`);
      const handlers = Array.isArray(found) ? found : [found];
      const index = Math.min(used[key] ?? 0, handlers.length - 1);
      used[key] = (used[key] ?? 0) + 1;
      return handlers[index]!(init);
    }),
  );
  return calls;
}

function renderPage() {
  render(
    <I18nProvider>
      <MemoryRouter initialEntries={["/conversations/p1"]}>
        <Routes>
          <Route path="/conversations/:id" element={<Conversations />} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: true, media: query, addEventListener: () => undefined, removeEventListener: () => undefined }));
});

afterEach(() => {
  vi.unstubAllGlobals();
  logout.mockClear();
});

describe("who is in charge", () => {
  it("shows the agent in charge, with a way to take over, and no box to write", async () => {
    stub({ [LIST]: () => json(200, list()), [THREAD]: () => json(200, thread(AGENT)) });
    renderPage();
    expect(await screen.findByTestId("control-banner")).toHaveTextContent("The agent is handling this conversation.");
    expect(screen.getByRole("button", { name: "Take over" })).toBeEnabled();
    expect(screen.queryByTestId("composer")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Give back to the agent" })).not.toBeInTheDocument();
  });

  it("shows nothing about control when the server does not tell (never guesses)", async () => {
    stub({ [LIST]: () => json(200, list()), [THREAD]: () => json(200, thread(null)) });
    renderPage();
    await screen.findByTestId("message-m1");
    expect(screen.queryByTestId("control-bar")).not.toBeInTheDocument();
    expect(screen.queryByTestId("composer")).not.toBeInTheDocument();
  });

  it("says a conversation waits for a person, with the reason, and offers to take it", async () => {
    stub({ [LIST]: () => json(200, list(true)), [THREAD]: () => json(200, thread(WAITING, { status: "paused" })) });
    renderPage();
    expect(await screen.findByTestId("control-banner")).toHaveTextContent("waiting for a person");
    expect(screen.getByTestId("control-reason")).toHaveTextContent("asks for a discount");
    expect(screen.getByRole("button", { name: "Take over" })).toBeInTheDocument();
    expect(screen.getByTestId("thread-status")).toHaveTextContent("Waiting for a person");
    expect(screen.getByTestId("needs-person-p1")).toBeInTheDocument();
  });

  it("takes the conversation over, then shows the box to write", async () => {
    const calls = stub({
      [LIST]: () => json(200, list()),
      [THREAD]: [() => json(200, thread(AGENT)), () => json(200, thread(TAKEN, { status: "paused" }))],
      [TAKE]: () => json(200, TAKEN),
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Take over" }));
    expect(await screen.findByTestId("composer")).toBeInTheDocument();
    expect(screen.getByTestId("control-banner")).toHaveTextContent("You are handling this conversation");
    expect(screen.getByRole("button", { name: "Give back to the agent" })).toBeInTheDocument();
    expect(calls.filter((c) => c.key === TAKE)).toHaveLength(1);
  });

  it("explains a take-over that fails and keeps the agent in charge on screen", async () => {
    stub({
      [LIST]: () => json(200, list()),
      [THREAD]: () => json(200, thread(AGENT)),
      [TAKE]: () => json(409, { detail: { code: "agent_not_loaded", message: "your agent is not loaded by the platform yet" } }),
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Take over" }));
    expect(await screen.findByTestId("control-error")).toHaveTextContent("not loaded or not ready");
    expect(screen.getByRole("button", { name: "Take over" })).toBeEnabled();
  });

  it("gives the conversation back and says when the agent will follow up", async () => {
    stub({
      [LIST]: () => json(200, list()),
      [THREAD]: [() => json(200, thread(TAKEN, { status: "paused" })), () => json(200, thread({ ...AGENT, released_at: "2026-10-10T10:00:00Z" }))],
      [RELEASE]: () => json(200, { released: true, control: AGENT, status: "active", next_follow_up_at: "2026-10-12T10:00:00Z" }),
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Give back to the agent" }));
    expect(await screen.findByTestId("control-notice")).toHaveTextContent("Next follow-up:");
    expect(screen.getByTestId("control-banner")).toHaveTextContent("The agent is handling this conversation.");
    expect(screen.queryByTestId("composer")).not.toBeInTheDocument();
  });

  it("tells a person who asked to stop that nothing will be sent after the give-back", async () => {
    stub({
      [LIST]: () => json(200, list()),
      [THREAD]: [() => json(200, thread(TAKEN, { status: "opted_out" })), () => json(200, thread(AGENT, { status: "opted_out" }))],
      [RELEASE]: () => json(200, { released: true, control: AGENT, status: "opted_out", next_follow_up_at: null }),
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Give back to the agent" }));
    expect(await screen.findByTestId("control-notice")).toHaveTextContent("asked to stop");
  });

  it("shows the next follow-up when the agent has one planned", async () => {
    stub({
      [LIST]: () => json(200, list()),
      [THREAD]: () => json(200, thread(AGENT, { nextFollowUp: { action_type: "follow_up", due_at: "2026-10-12T10:00:00Z" } })),
    });
    renderPage();
    expect(await screen.findByTestId("thread-follow-up")).toHaveTextContent("Next follow-up");
  });

  it("marks the messages a person wrote", async () => {
    stub({
      [LIST]: () => json(200, list()),
      [THREAD]: () =>
        json(
          200,
          thread(TAKEN, {
            messages: [
              { id: "m1", direction: "inbound", channel: "whatsapp", text: "Hi", timestamp: "2026-10-10T09:01:00Z", author: "prospect" },
              { id: "m2", direction: "outbound", channel: "whatsapp", text: "Hello, it is Sam", timestamp: "2026-10-10T09:02:00Z", author: "human" },
            ],
          }),
        ),
    });
    renderPage();
    const mine = await screen.findByTestId("message-m2");
    expect(mine).toHaveAttribute("data-author", "human");
    expect(mine).toHaveTextContent("Sent by you");
  });
});

describe("writing to the prospect", () => {
  const taken = () => ({ [LIST]: () => json(200, list()) });

  it("cannot send an empty message, and says why", async () => {
    stub({ ...taken(), [THREAD]: () => json(200, thread(TAKEN, { status: "paused" })) });
    renderPage();
    const box = await screen.findByTestId("composer");
    const send = within(box).getByRole("button", { name: "Send" });
    expect(send).toBeDisabled();
    expect(send).toHaveAttribute("title", "Write a message first.");
  });

  it("sends the message with an idempotency key, clears the box and reads the conversation again", async () => {
    const sentMessage = { id: "m2", direction: "outbound", channel: "whatsapp", text: "Hello, it is Sam", timestamp: "2026-10-10T09:02:00Z", author: "human" };
    const calls = stub({
      ...taken(),
      [THREAD]: [() => json(200, thread(TAKEN, { status: "paused" })), () => json(200, thread(TAKEN, { status: "paused", messages: [sentMessage] }))],
      [SEND]: () => json(201, { message_id: "out:x", channel: "whatsapp", sent_at: "2026-10-10T09:02:00Z", replayed: false }),
    });
    const user = userEvent.setup();
    renderPage();
    const box = await screen.findByLabelText("Your message to Amel");
    await user.type(box, "  Hello, it is Sam ");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByTestId("composer-sent")).toBeInTheDocument();
    expect(await screen.findByTestId("message-m2")).toHaveTextContent("Hello, it is Sam");
    expect(box).toHaveValue("");
    const post = calls.find((c) => c.key === SEND)!.body as { text: string; idempotency_key: string };
    expect(post.text).toBe("Hello, it is Sam");
    expect(post.idempotency_key).toMatch(/^msg-[A-Za-z0-9_.:-]{8,60}$/);
  });

  it("keeps the same key after a failure that may have gone through, a new one after a certain refusal", async () => {
    const calls = stub({
      ...taken(),
      [THREAD]: () => json(200, thread(TAKEN, { status: "paused" })),
      [SEND]: [
        () => json(503, { detail: "conversations are temporarily unavailable" }),
        () => json(409, { detail: { code: "not_now", message: "outside the 24-hour window" } }),
        () => json(409, { detail: { code: "not_now", message: "outside the 24-hour window" } }),
      ],
    });
    const user = userEvent.setup();
    renderPage();
    await user.type(await screen.findByLabelText("Your message to Amel"), "Hello");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByTestId("composer-error")).toHaveTextContent("could not be sent");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(calls.filter((c) => c.key === SEND)).toHaveLength(2));
    expect(await screen.findByTestId("composer-error")).toHaveTextContent("asked to wait");
    expect(screen.getByTestId("composer-error")).toHaveTextContent("outside the 24-hour window");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(calls.filter((c) => c.key === SEND)).toHaveLength(3));
    const keys = calls.filter((c) => c.key === SEND).map((c) => (c.body as { idempotency_key: string }).idempotency_key);
    expect(keys[1]).toBe(keys[0]); // the first failure was uncertain: same key, never sent twice
    expect(keys[2]).not.toBe(keys[1]); // the second was a certain refusal: a new message
    expect(screen.getByLabelText("Your message to Amel")).toHaveValue("Hello"); // nothing typed is lost
  });

  it.each([
    ["channel_not_ready", "not ready to send"],
    ["opted_out", "asked not to be contacted"],
    ["not_in_human_mode", "Take over the conversation"],
    ["unconfirmed", "could not confirm"],
  ])("explains the refusal %s", async (code, text) => {
    stub({
      ...taken(),
      [THREAD]: () => json(200, thread(TAKEN, { status: "paused" })),
      [SEND]: () => json(409, { detail: { code, message: "server words" } }),
    });
    const user = userEvent.setup();
    renderPage();
    await user.type(await screen.findByLabelText("Your message to Amel"), "Hello");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByTestId("composer-error")).toHaveTextContent(text);
  });

  it("closes the box for a person who asked to stop, and says why", async () => {
    stub({ ...taken(), [THREAD]: () => json(200, thread(TAKEN, { status: "opted_out" })) });
    renderPage();
    expect(await screen.findByTestId("composer-closed")).toHaveTextContent("asked not to be contacted");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("signs the client out when the session ended while sending", async () => {
    stub({
      ...taken(),
      [THREAD]: () => json(200, thread(TAKEN, { status: "paused" })),
      [SEND]: () => json(401, { detail: "not authenticated" }),
    });
    const user = userEvent.setup();
    renderPage();
    await user.type(await screen.findByLabelText("Your message to Amel"), "Hello");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(logout).toHaveBeenCalled());
  });
});
