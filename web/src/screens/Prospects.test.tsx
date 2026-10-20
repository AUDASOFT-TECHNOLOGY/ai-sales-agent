import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { Prospects } from "./Prospects";

const logout = vi.fn(async () => undefined);
vi.mock("../client/ClientAuthContext", () => ({ useClientAuth: () => ({ logout }) }));

function row(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    display_name: `Person ${id}`,
    status: "active",
    stage: "qualified",
    locale: "fr",
    lane_id: null,
    phone: "+21620000001",
    email: null,
    preferred_channel: null,
    next_action_type: "follow_up",
    next_action_due_at: "2026-10-20T09:00:00Z",
    last_contacted_at: "2026-10-10T09:00:00Z",
    last_inbound_at: null,
    follow_up_count: 1,
    ...over,
  };
}

function page(prospects: unknown[], total = prospects.length, offset = 0) {
  return { total, limit: 25, offset, prospects };
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

function renderPage(path = "/prospects") {
  render(
    <I18nProvider>
      <MemoryRouter initialEntries={[path]}>
        <Prospects />
      </MemoryRouter>
    </I18nProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  logout.mockClear();
});

describe("Prospects (live)", () => {
  it("shows what the server returns, and nothing else", async () => {
    const calls = stub({
      "GET /client/v1/prospects?limit=25&offset=0": () =>
        json(200, page([row("p1", { display_name: "Amel Ben Salah", status: "opted_out", stage: null, next_action_type: null, next_action_due_at: null })])),
    });
    renderPage();
    const line = await screen.findByTestId("prospect-p1");
    expect(within(line).getByText("Amel Ben Salah")).toBeInTheDocument();
    expect(within(line).getByText("Opted out")).toBeInTheDocument();
    expect(screen.getByText(/1 prospects in total/)).toBeInTheDocument();
    expect(within(line).getByRole("link")).toHaveAttribute("href", "/conversations/p1");
    expect(calls.filter((c) => c.includes("/prospects"))).toEqual(["GET /client/v1/prospects?limit=25&offset=0"]);
  });

  it("shows a loading state before the answer arrives", async () => {
    let release: (r: Response) => void = () => undefined;
    stub({ "GET /client/v1/prospects?limit=25&offset=0": () => new Promise<Response>((resolve) => (release = resolve)) });
    renderPage();
    expect(screen.getByRole("status")).toHaveTextContent("Loading your prospects");
    release(json(200, page([row("p1")])));
    expect(await screen.findByTestId("prospect-p1")).toBeInTheDocument();
  });

  it("says so when the client has no prospect", async () => {
    stub({ "GET /client/v1/prospects?limit=25&offset=0": () => json(200, page([])) });
    renderPage();
    expect(await screen.findByTestId("prospects-empty")).toHaveTextContent("no prospects yet");
  });

  it("asks the server for the next page and shows its prospects", async () => {
    const first = Array.from({ length: 25 }, (_, i) => row(`a${i}`));
    const calls = stub({
      "GET /client/v1/prospects?limit=25&offset=0": () => json(200, page(first, 27)),
      "GET /client/v1/prospects?limit=25&offset=25": () => json(200, page([row("z1"), row("z2")], 27, 25)),
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("prospect-a0");
    expect(screen.getByTestId("prospects-page")).toHaveTextContent(/Page\D+1\D+of\D+2/);
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByTestId("prospect-z2")).toBeInTheDocument();
    expect(screen.queryByTestId("prospect-a0")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    expect(calls).toContain("GET /client/v1/prospects?limit=25&offset=25");
  });

  it("shows the error and retries when the server fails", async () => {
    let reads = 0;
    stub({
      "GET /client/v1/prospects?limit=25&offset=0": () =>
        reads++ === 0 ? json(503, { detail: "prospects are temporarily unavailable" }) : json(200, page([row("p1")])),
    });
    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
    expect(screen.queryByTestId("prospect-p1")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("prospect-p1")).toBeInTheDocument();
  });

  it("explains a 409 (agent not ready) instead of showing an empty list", async () => {
    stub({ "GET /client/v1/prospects?limit=25&offset=0": () => json(409, { detail: "your agent is not ready: see your readiness checks" }) });
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("not loaded or not ready");
    expect(screen.queryByTestId("prospects-empty")).not.toBeInTheDocument();
  });

  it("signs the client out when the session has ended", async () => {
    stub({ "GET /client/v1/prospects?limit=25&offset=0": () => json(401, { detail: "not authenticated" }) });
    renderPage();
    await waitFor(() => expect(logout).toHaveBeenCalled());
  });

  it("searches on the server and keeps the page in the URL, back to page 1", async () => {
    const calls = stub({
      "GET /client/v1/prospects?limit=25&offset=0": () => json(200, page([row("p1", { display_name: "Amel" }), row("p2", { display_name: "Zied" })])),
      "GET /client/v1/prospects?limit=25&offset=0&q=zied": () => json(200, page([row("p2", { display_name: "Zied" })])),
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("prospect-p1");
    await user.type(screen.getByRole("searchbox"), "zied");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(screen.queryByTestId("prospect-p1")).not.toBeInTheDocument());
    expect(screen.getByTestId("prospect-p2")).toBeInTheDocument();
    expect(screen.getByText(/1 matching/)).toBeInTheDocument();
    expect(calls).toContain("GET /client/v1/prospects?limit=25&offset=0&q=zied");
  });

  it("sends every filter to the server and says when nothing matches", async () => {
    const wanted = "GET /client/v1/prospects?limit=25&offset=0&status=paused&channel=email&needs_human=true&has_error=true";
    const calls = stub({
      "GET /client/v1/prospects?limit=25&offset=0": () => json(200, page([row("p1")])),
      [wanted]: () => json(200, page([])),
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("prospect-p1");
    await user.selectOptions(screen.getByLabelText("Status"), "paused");
    await user.selectOptions(screen.getByLabelText("Channel"), "email");
    await user.click(screen.getByLabelText("Needs a person"));
    await user.click(screen.getByLabelText("With errors"));
    expect(await screen.findByTestId("prospects-no-match")).toHaveTextContent("No prospect matches");
    expect(calls).toContain(wanted);
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByTestId("prospect-p1")).toBeInTheDocument();
  });

  it("restores the filters and the page from the URL, with a period in UTC days", async () => {
    const calls = stub({
      "GET /client/v1/prospects?limit=25&offset=25&q=amel&status=active&active_since=2026-10-01T00%3A00%3A00Z&active_until=2026-10-11T00%3A00%3A00.000Z": () =>
        json(200, page([row("z1")], 27, 25)),
    });
    renderPage("/prospects?page=2&q=amel&status=active&since=2026-10-01&until=2026-10-10");
    expect(await screen.findByTestId("prospect-z1")).toBeInTheDocument();
    expect(calls.length).toBeGreaterThan(0);
  });
});
