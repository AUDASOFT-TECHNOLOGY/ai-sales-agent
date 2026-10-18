import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { ClientLanesPage } from "./ClientLanesPage";

const logout = vi.fn(async () => undefined);
vi.mock("./ClientAuthContext", () => ({ useClientAuth: () => ({ logout }) }));

type Json = Record<string, unknown>;

function lane(id: string, over: Json = {}) {
  return {
    id,
    position: 1,
    source: {
      id,
      source: "google_sheets",
      label: "Google Sheets",
      google_sheets: { spreadsheet_id: `sheet-${id}`, prospects_tab: "Prospects" },
    },
    channel: { channel: "email", label: "Email", authorized: true, problem: null },
    products: ["ALL"],
    offers_all_products: true,
    ...over,
  };
}

function lanesState(over: Json = {}) {
  return {
    version: 3,
    editable: true,
    lanes: [lane("alpha"), lane("beta", { position: 2 })],
    source_options: [
      { key: "google_sheets", label: "Google Sheets" },
      { key: "hubspot", label: "HubSpot" },
    ],
    channel_options: [
      { key: "email", label: "Email" },
      { key: "telegram", label: "Telegram" },
    ],
    share_with: "revansa@platform.iam.gserviceaccount.com",
    catalog: [
      { id: "P1", name: "Solar kit" },
      { id: "P2", name: null },
    ],
    ...over,
  };
}

const READY = { ready: true, checks: [{ id: "source_connected", ok: true, message: null }] };
const NOT_READY = {
  ready: false,
  checks: [
    { id: "source_connected", ok: false, message: "Test the connection to your sources." },
    { id: "channel_ready", ok: true, message: null },
  ],
};
const SOURCE = { version: 3, access: null, last_test: null };

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

const bodyOf = (init: RequestInit | undefined) => JSON.parse(String(init?.body)) as Json;

const LANES = "GET /client/v1/lanes";
const READINESS = "GET /client/v1/readiness";
const SOURCE_ROUTE = "GET /client/v1/source";

function reads(state: unknown = lanesState(), readiness: unknown = NOT_READY, source: unknown = SOURCE) {
  return {
    [LANES]: () => json(200, state),
    [READINESS]: () => json(200, readiness),
    [SOURCE_ROUTE]: () => json(200, source),
  };
}

function renderPage() {
  render(
    <I18nProvider>
      <MemoryRouter>
        <ClientLanesPage />
      </MemoryRouter>
    </I18nProvider>,
  );
}

beforeEach(() => logout.mockClear());
afterEach(() => vi.unstubAllGlobals());

describe("ClientLanesPage: reading", () => {
  it("lists each lane with its source, channel and products", async () => {
    stub(reads(lanesState({ lanes: [lane("alpha", { products: ["P1", "P2"], offers_all_products: false }), lane("beta", { position: 2 })] })));
    renderPage();
    const alpha = await screen.findByTestId("lane-alpha");
    expect(within(alpha).getByText("Google Sheets")).toBeInTheDocument();
    expect(within(alpha).getByText(/sheet-alpha/)).toBeInTheDocument();
    expect(within(alpha).getByText("Email")).toBeInTheDocument();
    expect(within(alpha).getByText("Solar kit")).toBeInTheDocument();
    expect(within(alpha).getByText("P2")).toBeInTheDocument();
    expect(within(screen.getByTestId("lane-beta")).getByText("All products")).toBeInTheDocument();
  });

  it("shows what the API says is wrong with a channel, and a channel the platform does not allow", async () => {
    stub(
      reads(
        lanesState({
          lanes: [
            lane("alpha", { channel: { channel: "email", label: "Email", authorized: true, problem: "Add your mailbox password." } }),
            lane("beta", { position: 2, channel: { channel: "telegram", label: "Telegram", authorized: false, problem: null } }),
          ],
        }),
      ),
    );
    renderPage();
    const alpha = await screen.findByTestId("lane-alpha");
    expect(within(alpha).getByText("Add your mailbox password.")).toBeInTheDocument();
    expect(within(alpha).getByText("Channel needs attention")).toBeInTheDocument();
    expect(within(screen.getByTestId("lane-beta")).getByText("Channel not allowed")).toBeInTheDocument();
  });

  it("never says the configuration is complete unless the server does", async () => {
    stub(reads(lanesState(), NOT_READY));
    renderPage();
    expect(await screen.findByTestId("readiness-incomplete")).toHaveTextContent("Test the connection to your sources.");
    expect(screen.queryByTestId("readiness-ready")).not.toBeInTheDocument();
  });

  it("says the configuration is complete when the server says so", async () => {
    stub(reads(lanesState(), READY));
    renderPage();
    expect(await screen.findByTestId("readiness-ready")).toHaveTextContent("Your configuration is complete.");
  });

  it("claims nothing when readiness cannot be read", async () => {
    stub({ ...reads(), [READINESS]: () => json(503, { detail: "x" }) });
    renderPage();
    expect(await screen.findByTestId("readiness-unknown")).toBeInTheDocument();
    expect(screen.queryByTestId("readiness-ready")).not.toBeInTheDocument();
    expect(screen.getByTestId("lane-alpha")).toBeInTheDocument();
  });

  it("shows the implicit default lane read-only and blocks adding", async () => {
    stub(reads(lanesState({ editable: false, lanes: [lane("default", { source: { id: "google_sheets", source: "google_sheets", label: "Google Sheets", google_sheets: null } })] })));
    renderPage();
    const card = await screen.findByTestId("lane-default");
    expect(screen.getByTestId("lanes-implicit")).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add a lane" })).toBeDisabled();
  });

  it("says plainly when there is no lane", async () => {
    stub(reads(lanesState({ lanes: [] })));
    renderPage();
    expect(await screen.findByTestId("lanes-empty")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add a lane" })).toBeEnabled();
  });

  it("explains a network failure and lets the client retry", async () => {
    let up = false;
    stub({
      ...reads(),
      [LANES]: () => {
        if (!up) throw new TypeError("network");
        return json(200, lanesState());
      },
    });
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
    up = true;
    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("lane-alpha")).toBeInTheDocument();
  });

  it("signs the client out when the session has expired", async () => {
    stub({ ...reads(), [LANES]: () => json(401, { detail: "no session" }) });
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("Your session has expired");
    expect(logout).toHaveBeenCalled();
  });
});

describe("ClientLanesPage: creating and editing", () => {
  it("creates a Google Sheets lane with the version it was based on", async () => {
    let posted: Json | null = null;
    stub({
      ...reads(),
      "POST /client/v1/lanes": (init) => {
        posted = bodyOf(init);
        return json(201, lane("gamma", { position: 3 }));
      },
    });
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a lane" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Lane name/), "gamma");
    await user.type(within(dialog).getByLabelText(/Spreadsheet link or identifier/), "https://docs.google.com/spreadsheets/d/abc123/edit");
    await user.click(within(dialog).getByRole("radio", { name: "Telegram" }));
    await user.click(within(dialog).getByRole("button", { name: "Add the lane" }));
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toEqual({
      expected_version: 3,
      id: "gamma",
      channel: "telegram",
      products: ["ALL"],
      source: { id: "gamma", provider: "google_sheets", google_sheets: { spreadsheet: "https://docs.google.com/spreadsheets/d/abc123/edit" } },
    });
    expect(await screen.findByText(/Lane added/)).toBeInTheDocument();
  });

  it("offers only the channels the platform allows", async () => {
    stub(reads(lanesState({ channel_options: [{ key: "email", label: "Email" }] })));
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a lane" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getAllByRole("radio", { name: /Email|Telegram|WhatsApp/ })).toHaveLength(1);
    expect(within(dialog).queryByRole("radio", { name: "Telegram" })).not.toBeInTheDocument();
  });

  it("does not ask a HubSpot lane for any mapping and sends no spreadsheet", async () => {
    let posted: Json | null = null;
    stub({
      ...reads(),
      "POST /client/v1/lanes": (init) => {
        posted = bodyOf(init);
        return json(201, lane("crm", { source: { id: "crm", source: "hubspot", label: "HubSpot", google_sheets: null } }));
      },
    });
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a lane" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Lane name/), "crm");
    await user.click(within(dialog).getByRole("radio", { name: "HubSpot" }));
    expect(within(dialog).queryByLabelText(/Spreadsheet/)).not.toBeInTheDocument();
    expect(within(dialog).getByText(/set by your administrator/)).toBeInTheDocument();
    expect(within(dialog).queryByLabelText(/status|mapping/i)).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Add the lane" }));
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toEqual({
      expected_version: 3,
      id: "crm",
      channel: "email",
      products: ["ALL"],
      source: { id: "crm", provider: "hubspot" },
    });
  });

  it("sends the chosen products, and refuses to submit a lane with none", async () => {
    let posted: Json | null = null;
    stub({
      ...reads(),
      "POST /client/v1/lanes": (init) => {
        posted = bodyOf(init);
        return json(201, lane("gamma"));
      },
    });
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a lane" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Lane name/), "gamma");
    await user.type(within(dialog).getByLabelText(/Spreadsheet link or identifier/), "abc123");
    await user.click(within(dialog).getByRole("switch", { name: "All products" }));
    expect(within(dialog).getByRole("button", { name: "Add the lane" })).toBeDisabled();
    await user.click(within(dialog).getByRole("button", { name: "Solar kit" }));
    await user.click(within(dialog).getByRole("button", { name: "Add the lane" }));
    await waitFor(() => expect(posted).not.toBeNull());
    expect((posted as unknown as Json).products).toEqual(["P1"]);
  });

  it("shows every problem the server found and keeps the form open", async () => {
    stub({
      ...reads(),
      "POST /client/v1/lanes": () => json(422, { detail: ["unknown product 'P9'", "lane 'gamma': unknown channel"] }),
    });
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a lane" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Lane name/), "gamma");
    await user.type(within(dialog).getByLabelText(/Spreadsheet link or identifier/), "abc");
    await user.click(within(dialog).getByRole("button", { name: "Add the lane" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("unknown product 'P9'");
    expect(alert).toHaveTextContent("unknown channel");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("sends only what changed when editing, and never the lane's id or source", async () => {
    let put: Json | null = null;
    stub({
      ...reads(),
      "PUT /client/v1/lanes/alpha": (init) => {
        put = bodyOf(init);
        return json(200, lane("alpha"));
      },
    });
    renderPage();
    const user = userEvent.setup();
    await user.click(within(await screen.findByTestId("lane-alpha")).getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(within(dialog).queryByLabelText(/Lane name/)).toBeNull(); // locked, not editable
    const tab = within(dialog).getByLabelText(/Prospects tab/);
    await user.clear(tab);
    await user.type(tab, "Leads");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(put).not.toBeNull());
    expect(put).toEqual({ expected_version: 3, google_sheets: { prospects_tab: "Leads" } });
  });

  it("warns that a lane's channel is not allowed and what to do", async () => {
    stub(reads(lanesState({ lanes: [lane("alpha", { channel: { channel: "whatsapp", label: "WhatsApp", authorized: false, problem: null } })] })));
    renderPage();
    const user = userEvent.setup();
    await user.click(within(await screen.findByTestId("lane-alpha")).getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/does not allow this channel/)).toBeInTheDocument();
  });
});

describe("ClientLanesPage: deleting", () => {
  it("asks for confirmation, then deletes with the version", async () => {
    let state = lanesState();
    const calls = stub({
      ...reads(),
      [LANES]: () => json(200, state),
      "DELETE /client/v1/lanes/beta?expected_version=3": () => {
        state = lanesState({ version: 4, lanes: [lane("alpha")] });
        return json(200, state);
      },
    });
    renderPage();
    const user = userEvent.setup();
    await user.click(within(await screen.findByTestId("lane-beta")).getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    expect(calls.some((c) => c.startsWith("DELETE"))).toBe(false); // nothing is sent before the confirmation
    expect(within(dialog).getByText(/will be paused/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Delete the lane" }));
    expect(await screen.findByText(/Lane deleted/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("lane-beta")).not.toBeInTheDocument());
    expect(calls).toContain("DELETE /client/v1/lanes/beta?expected_version=3");
  });

  it("never offers to delete the last lane", async () => {
    stub(reads(lanesState({ lanes: [lane("alpha")] })));
    renderPage();
    expect(within(await screen.findByTestId("lane-alpha")).getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("shows a version conflict, reloads, and does not hide it", async () => {
    let state = lanesState();
    stub({
      ...reads(),
      [LANES]: () => json(200, state),
      "DELETE /client/v1/lanes/beta?expected_version=3": () => {
        state = lanesState({ version: 4, lanes: [lane("alpha"), lane("beta", { position: 2 }), lane("gamma", { position: 3 })] });
        return json(409, { detail: "the settings changed since they were loaded; reload and retry" });
      },
    });
    renderPage();
    const user = userEvent.setup();
    await user.click(within(await screen.findByTestId("lane-beta")).getByRole("button", { name: "Delete" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete the lane" }));
    const alert = await within(screen.getByRole("dialog")).findByRole("alert");
    expect(alert).toHaveTextContent("The list has been refreshed");
    expect(alert).toHaveTextContent("the settings changed since they were loaded");
    expect(await screen.findByTestId("lane-gamma")).toBeInTheDocument(); // the list now shows what the server holds
  });
});

describe("ClientLanesPage: connections", () => {
  it("shows the result of each lane's connection test and does not call a failing lane connected", async () => {
    stub({
      ...reads(lanesState(), NOT_READY),
      "POST /client/v1/source/test": () =>
        json(200, {
          source: "lanes",
          ok: false,
          message: "These lanes cannot reach their source: beta",
          details: {},
          checked_at: "2026-10-15T10:00:00Z",
          lanes: [
            { lane: "alpha", ok: true, message: "The spreadsheet answered." },
            { lane: "beta", ok: false, message: "The spreadsheet could not be opened." },
          ],
        }),
    });
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Test connections" }));
    const result = await screen.findByTestId("test-result");
    expect(result).toHaveTextContent("These lanes cannot reach their source: beta");
    expect(within(screen.getByTestId("lane-alpha")).getByText("Connection test passed")).toBeInTheDocument();
    const beta = screen.getByTestId("lane-beta");
    expect(within(beta).getByText("Connection test failed")).toBeInTheDocument();
    expect(within(beta).queryByText("Connection test passed")).not.toBeInTheDocument();
    expect(screen.queryByTestId("readiness-ready")).not.toBeInTheDocument();
  });

  it("says a test was just made when the server rate-limits it", async () => {
    stub({ ...reads(), "POST /client/v1/source/test": () => json(429, { detail: "a test was just made: wait a few seconds" }) });
    renderPage();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Test connections" }));
    expect(await screen.findByText(/A test was just made/)).toBeInTheDocument();
  });

  it("marks an old stored test as outdated instead of relying on it", async () => {
    stub(reads(lanesState(), NOT_READY, { version: 3, access: null, last_test: { ok: true, checked_at: "2026-10-14T10:00:00Z", current: false } }));
    renderPage();
    expect(await screen.findByText(/This test is outdated/)).toBeInTheDocument();
    expect(within(screen.getByTestId("lane-alpha")).getByText("Connection not tested")).toBeInTheDocument();
  });

  it("tells a HubSpot lane that the platform is connecting it and asks for no token", async () => {
    const calls = stub({
      ...reads(lanesState({ lanes: [lane("crm", { source: { id: "crm", source: "hubspot", label: "HubSpot", google_sheets: null } })] })),
      [SOURCE_ROUTE]: () => json(200, { version: 3, access: "waiting_platform", last_test: null }),
    });
    renderPage();
    expect(await screen.findByTestId("access-status")).toHaveTextContent("being connected by Revansa");
    expect(screen.queryByLabelText("Access token")).not.toBeInTheDocument();
    expect(document.querySelectorAll('input[type="password"]')).toHaveLength(0);
    expect(calls.some((key) => key.includes("credentials"))).toBe(false);
  });

  it("says nothing about a connection once the platform has made it", async () => {
    stub({
      ...reads(lanesState({ lanes: [lane("crm", { source: { id: "crm", source: "hubspot", label: "HubSpot", google_sheets: null } })] })),
      [SOURCE_ROUTE]: () => json(200, { version: 3, access: "ready", last_test: null }),
    });
    renderPage();
    await screen.findByTestId("lane-crm");
    expect(screen.queryByTestId("access-status")).not.toBeInTheDocument();
  });

  it("offers no token field when no lane uses HubSpot", async () => {
    stub(reads());
    renderPage();
    await screen.findByTestId("lane-alpha");
    expect(screen.queryByLabelText("Access token")).not.toBeInTheDocument();
    expect(screen.queryByTestId("access-status")).not.toBeInTheDocument();
  });
});

describe("ClientLanesPage: sharing the spreadsheet", () => {
  it("tells the client which address to share a spreadsheet with", async () => {
    stub(reads());
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a lane" }));
    expect(await screen.findByTestId("share-with")).toHaveTextContent("revansa@platform.iam.gserviceaccount.com");
  });

  it("invents no address when the server has none", async () => {
    stub(reads(lanesState({ share_with: null })));
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a lane" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByTestId("share-with")).not.toBeInTheDocument();
    expect(within(dialog).getByText(/ask your administrator/)).toBeInTheDocument();
  });

  it("does not show an address for a HubSpot lane", async () => {
    stub(reads());
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a lane" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("radio", { name: "HubSpot" }));
    expect(within(dialog).queryByTestId("share-with")).not.toBeInTheDocument();
  });
});
