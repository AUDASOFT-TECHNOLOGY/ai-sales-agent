import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { ClientApprovalsPage } from "./ClientApprovalsPage";

const logout = vi.fn(async () => undefined);
vi.mock("./ClientAuthContext", () => ({ useClientAuth: () => ({ logout }) }));

function proposal(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    prospect_name: `Prospect ${id}`,
    text: `Hello ${id}, a message from your agent.`,
    language: "en",
    due_at: "2026-10-13T09:00:00Z",
    ...over,
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Handler = (init: RequestInit | undefined) => Response | Promise<Response>;

/** Only fetch is simulated (the server is not reachable from a unit test): the page, its routes and
 *  its state handling are the real ones. */
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

function renderPage() {
  render(
    <I18nProvider>
      <MemoryRouter>
        <ClientApprovalsPage />
      </MemoryRouter>
    </I18nProvider>,
  );
}

const LIST = "GET /client/v1/approvals";

beforeEach(() => logout.mockClear());
afterEach(() => vi.unstubAllGlobals());

describe("ClientApprovalsPage", () => {
  it("shows each waiting proposal with its prospect, text and language", async () => {
    stub({ [LIST]: () => json(200, { proposals: [proposal("a1"), proposal("a2", { prospect_name: null, language: null })] }) });
    renderPage();
    const first = await screen.findByRole("article", { name: "Prospect a1" });
    expect(within(first).getByText("Hello a1, a message from your agent.")).toBeInTheDocument();
    expect(within(first).getByText(/· en/)).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Unknown prospect" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Approve" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Reject" })).toHaveLength(2);
  });

  it("says plainly when nothing is waiting", async () => {
    stub({ [LIST]: () => json(200, { proposals: [] }) });
    renderPage();
    expect(await screen.findByTestId("approvals-empty")).toHaveTextContent("Nothing is waiting for your approval.");
  });

  it("renders a draft as text, never as markup", async () => {
    stub({ [LIST]: () => json(200, { proposals: [proposal("a1", { text: "<img src=x onerror=alert(1)> hi" })] }) });
    renderPage();
    expect(await screen.findByText("<img src=x onerror=alert(1)> hi")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("approves one proposal, sends only its id, then shows the list the server holds now", async () => {
    let waiting = [proposal("a1"), proposal("a2")];
    const calls = stub({
      [LIST]: () => json(200, { proposals: waiting }),
      "POST /client/v1/approvals/a1/approve": () => {
        waiting = [proposal("a2")];
        return json(200, { id: "a1", decision: "approved" });
      },
    });
    renderPage();
    const user = userEvent.setup();
    await user.click(within(await screen.findByRole("article", { name: "Prospect a1" })).getByRole("button", { name: "Approve" }));
    expect(await screen.findByText(/Message approved/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("article", { name: "Prospect a1" })).not.toBeInTheDocument());
    expect(screen.getByRole("article", { name: "Prospect a2" })).toBeInTheDocument();
    expect(calls).toEqual([LIST, "POST /client/v1/approvals/a1/approve", LIST]);
  });

  it("rejects a proposal and says it will not be sent", async () => {
    let waiting = [proposal("a1")];
    stub({
      [LIST]: () => json(200, { proposals: waiting }),
      "POST /client/v1/approvals/a1/reject": () => {
        waiting = [];
        return json(200, { id: "a1", decision: "rejected" });
      },
    });
    renderPage();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Reject" }));
    expect(await screen.findByText(/It will not be sent/)).toBeInTheDocument();
    expect(await screen.findByTestId("approvals-empty")).toBeInTheDocument();
  });

  it("blocks every decision while one is in flight, so nothing is decided twice", async () => {
    let release: (value: Response) => void = () => undefined;
    const calls = stub({
      [LIST]: () => json(200, { proposals: [proposal("a1"), proposal("a2")] }),
      "POST /client/v1/approvals/a1/approve": () => new Promise<Response>((resolve) => (release = resolve)),
    });
    renderPage();
    const user = userEvent.setup();
    await user.click(within(await screen.findByRole("article", { name: "Prospect a1" })).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(screen.getByRole("article", { name: "Prospect a2" })).toBeInTheDocument());
    for (const button of screen.getAllByRole("button", { name: /Approve|Reject/ })) expect(button).toBeDisabled();
    release(json(200, { id: "a1", decision: "approved" }));
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Reject" })[0]).toBeEnabled());
    expect(calls.filter((c) => c.startsWith("POST"))).toHaveLength(1);
  });

  it("explains a proposal that was already decided and refreshes the list", async () => {
    let waiting = [proposal("a1")];
    stub({
      [LIST]: () => json(200, { proposals: waiting }),
      "POST /client/v1/approvals/a1/approve": () => {
        waiting = [];
        return json(404, { detail: "no proposal is waiting under this id" });
      },
    });
    renderPage();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("no longer waiting");
    expect(await screen.findByTestId("approvals-empty")).toBeInTheDocument();
  });

  it("explains a prospect who can no longer be contacted, and still lets the client reject", async () => {
    stub({
      [LIST]: () => json(200, { proposals: [proposal("a1")] }),
      "POST /client/v1/approvals/a1/approve": () => json(409, { detail: "this prospect can no longer be contacted" }),
    });
    renderPage();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("You can still reject it");
    expect(screen.getByRole("article", { name: "Prospect a1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled();
  });

  it("keeps the proposal and says so when the server is unavailable or unreachable", async () => {
    stub({
      [LIST]: () => json(200, { proposals: [proposal("a1")] }),
      "POST /client/v1/approvals/a1/approve": () => json(503, { detail: "proposals are temporarily unavailable" }),
    });
    renderPage();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
    expect(screen.getByRole("article", { name: "Prospect a1" })).toBeInTheDocument();
  });

  it("reports a load failure, then recovers on refresh", async () => {
    let up = false;
    stub({ [LIST]: () => (up ? json(200, { proposals: [proposal("a1")] }) : json(409, { detail: "your agent is not ready" })) });
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("agent is not ready yet");
    up = true;
    await userEvent.setup().click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByRole("article", { name: "Prospect a1" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("ends the session when the server says it has expired", async () => {
    stub({ [LIST]: () => json(401, { detail: "session expired or invalid" }) });
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("Your session has expired");
    expect(logout).toHaveBeenCalledTimes(1);
  });

  it("never puts a client id or anything but the proposal id in a request", async () => {
    const calls = stub({
      [LIST]: () => json(200, { proposals: [proposal("a/1?x")] }),
      "POST /client/v1/approvals/a%2F1%3Fx/reject": () => json(200, { id: "a/1?x", decision: "rejected" }),
    });
    renderPage();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Reject" }));
    await waitFor(() => expect(calls).toContain("POST /client/v1/approvals/a%2F1%3Fx/reject"));
    expect(calls.every((c) => !/client_id|clientId/.test(c))).toBe(true);
  });
});
