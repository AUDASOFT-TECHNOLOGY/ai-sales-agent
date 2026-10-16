import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { Dashboard } from "./Dashboard";

const logout = vi.fn(async () => undefined);
vi.mock("../client/ClientAuthContext", () => ({ useClientAuth: () => ({ logout, profile: { name: "Acme Corp" } }) }));

const SUMMARY = {
  total_prospects: 7,
  by_status: { new: 1, active: 3, paused: 1, opted_out: 1, closed: 1 },
  upcoming_actions: { scheduled: 3, by_type: { follow_up: 2, callback: 1 }, next_due_at: "2026-10-20T09:00:00Z" },
};
const EMPTY = {
  total_prospects: 0,
  by_status: { new: 0, active: 0, paused: 0, opted_out: 0, closed: 0 },
  upcoming_actions: { scheduled: 0, by_type: {}, next_due_at: null },
};
const AGENT = { state: "running", active: true, ready: true, changed_at: null, reasons: [] };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Handler = () => Response | Promise<Response>;

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
      return handler();
    }),
  );
  return calls;
}

function renderPage() {
  render(
    <I18nProvider>
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    </I18nProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  logout.mockClear();
});

describe("Dashboard (live)", () => {
  it("shows the server's counts and the agent's real state, from the two real endpoints only", async () => {
    const calls = stub({
      "GET /client/v1/summary": () => json(200, SUMMARY),
      "GET /client/v1/agent": () => json(200, AGENT),
    });
    renderPage();
    expect(await screen.findByTestId("kpi-total")).toHaveTextContent("7");
    expect(screen.getByTestId("kpi-active")).toHaveTextContent("3");
    expect(screen.getByTestId("kpi-opted_out")).toHaveTextContent("1");
    const upcoming = screen.getByTestId("upcoming");
    expect(within(upcoming).getByText("Follow-up")).toBeInTheDocument();
    expect(within(upcoming).getByText("Callback")).toBeInTheDocument();
    expect(await screen.findByText("Working")).toBeInTheDocument();
    expect(screen.getByText("Good morning, Acme Corp.")).toBeInTheDocument();
    expect(screen.queryByText(/revenue/i)).not.toBeInTheDocument();
    expect([...calls].sort()).toEqual(["GET /client/v1/agent", "GET /client/v1/summary"]);
  });

  it("shows a loading state before the answer arrives", async () => {
    let release: (r: Response) => void = () => undefined;
    stub({
      "GET /client/v1/summary": () => new Promise<Response>((resolve) => (release = resolve)),
      "GET /client/v1/agent": () => json(200, AGENT),
    });
    renderPage();
    expect(screen.getByRole("status")).toHaveTextContent("Loading your figures");
    release(json(200, SUMMARY));
    expect(await screen.findByTestId("kpi-total")).toBeInTheDocument();
  });

  it("says so when the client has no prospect, without showing zeros as figures", async () => {
    stub({ "GET /client/v1/summary": () => json(200, EMPTY), "GET /client/v1/agent": () => json(200, { ...AGENT, state: "not_started", active: false }) });
    renderPage();
    expect(await screen.findByTestId("dashboard-empty")).toHaveTextContent("no prospects yet");
    expect(screen.queryByTestId("kpi-total")).not.toBeInTheDocument();
    expect(await screen.findByText("Not started")).toBeInTheDocument();
  });

  it("shows the error and retries when the server fails, never zeros", async () => {
    let reads = 0;
    stub({
      "GET /client/v1/summary": () => (reads++ === 0 ? json(503, { detail: "prospects are temporarily unavailable" }) : json(200, SUMMARY)),
      "GET /client/v1/agent": () => json(200, AGENT),
    });
    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
    expect(screen.queryByTestId("kpi-total")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("kpi-total")).toHaveTextContent("7");
  });

  it("explains a 409 (agent not ready) instead of showing an empty dashboard", async () => {
    stub({
      "GET /client/v1/summary": () => json(409, { detail: "your agent is not ready: see your readiness checks" }),
      "GET /client/v1/agent": () => json(200, { ...AGENT, active: false, ready: false }),
    });
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("not loaded or not ready");
    expect(screen.queryByTestId("dashboard-empty")).not.toBeInTheDocument();
    expect(await screen.findByText("Started, but not working yet")).toBeInTheDocument();
  });

  it("says the agent status is unavailable when its own read fails", async () => {
    stub({ "GET /client/v1/summary": () => json(200, SUMMARY), "GET /client/v1/agent": () => json(503, { detail: "x" }) });
    renderPage();
    expect(await screen.findByText("Agent status unavailable")).toBeInTheDocument();
  });

  it("signs the client out when the session has ended", async () => {
    stub({ "GET /client/v1/summary": () => json(401, { detail: "not authenticated" }), "GET /client/v1/agent": () => json(401, { detail: "x" }) });
    renderPage();
    await waitFor(() => expect(logout).toHaveBeenCalled());
  });
});
