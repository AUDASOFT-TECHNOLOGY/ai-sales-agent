import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { ClientAgentPage } from "./ClientAgentPage";

const logout = vi.fn(async () => undefined);
vi.mock("./ClientAuthContext", () => ({ useClientAuth: () => ({ logout }) }));

const REASONS = [
  { id: "source_connected", ok: false, message: "Test the connection to your sources." },
  { id: "whatsapp_number", ok: false, message: "Your WhatsApp number is missing." },
];

function agent(over: Record<string, unknown> = {}) {
  return { state: "not_started", active: false, ready: true, changed_at: null, reasons: [], ...over };
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

function renderPage() {
  render(
    <I18nProvider>
      <MemoryRouter>
        <ClientAgentPage />
      </MemoryRouter>
    </I18nProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  logout.mockClear();
});

describe("ClientAgentPage", () => {
  it("shows the state the server reports and explains why the agent cannot start, without letting it", async () => {
    const calls = stub({ "GET /client/v1/agent": () => json(200, agent({ ready: false, reasons: REASONS })) });
    renderPage();
    expect(await screen.findByTestId("agent-state")).toHaveTextContent("Not started");
    const missing = screen.getByTestId("agent-not-ready");
    expect(missing).toHaveTextContent("Test the connection to your sources.");
    expect(missing).toHaveTextContent("Your WhatsApp number is missing.");
    expect(screen.getByRole("button", { name: "Start the agent" })).toBeDisabled();
    expect(calls).toEqual(["GET /client/v1/agent"]); // no start call was made
  });

  it("starts the agent and confirms only with what the server answered", async () => {
    let release: (r: Response) => void = () => undefined;
    stub({
      "GET /client/v1/agent": () => json(200, agent()),
      "POST /client/v1/agent/start": () => new Promise<Response>((resolve) => (release = resolve)),
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Start the agent" }));
    expect(screen.queryByText(/confirmed that your agent is started/)).not.toBeInTheDocument();
    release(json(200, agent({ state: "running", active: true, changed_at: "2026-10-16T08:00:00Z" })));
    expect(await screen.findByText(/confirmed that your agent is started/)).toBeInTheDocument();
    expect(screen.getByTestId("agent-state")).toHaveTextContent("Working");
    expect(screen.getByRole("button", { name: "Pause the agent" })).toBeInTheDocument();
  });

  it("lists what the server says is missing when it refuses to start, and claims no start", async () => {
    let reads = 0;
    stub({
      // The page looked ready, then something broke before the click: the server has the last word.
      "GET /client/v1/agent": () => json(200, reads++ === 0 ? agent() : agent({ ready: false, reasons: REASONS })),
      "POST /client/v1/agent/start": () => json(409, { detail: { message: "your agent cannot start yet", reasons: REASONS } }),
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Start the agent" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("refused to start your agent");
    expect(screen.getByTestId("agent-not-ready")).toHaveTextContent("Your WhatsApp number is missing.");
    expect(screen.queryByText(/confirmed that your agent is started/)).not.toBeInTheDocument();
    expect(screen.getByTestId("agent-state")).toHaveTextContent("Not started");
  });

  it("pauses a running agent", async () => {
    const calls = stub({
      "GET /client/v1/agent": () => json(200, agent({ state: "running", active: true })),
      "POST /client/v1/agent/pause": () => json(200, agent({ state: "paused" })),
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pause the agent" }));
    expect(await screen.findByText(/confirmed that your agent is paused/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume the agent" })).toBeInTheDocument();
    expect(calls).toContain("POST /client/v1/agent/pause");
  });

  it("does not show a state it could not read, and offers a retry", async () => {
    stub({ "GET /client/v1/agent": () => json(503, { detail: "the agent switch is temporarily unavailable" }) });
    renderPage();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByTestId("agent-state")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("signs the client out when the session has ended", async () => {
    stub({ "GET /client/v1/agent": () => json(401, { detail: "not authenticated" }) });
    renderPage();
    await waitFor(() => expect(logout).toHaveBeenCalled());
  });
});
