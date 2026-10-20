import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../../i18n";
import { ClientAuthProvider } from "../ClientAuthContext";
import { SyncCard } from "./SyncCard";

const RESULTS = ["status", "last_contacted_at", "last_inbound_at", "follow_up_count", "next_action_type", "next_action_due_at"].map((key) => ({ key, label: key }));

function sync(over: Record<string, unknown> = {}) {
  return {
    version: 3,
    lane: "main",
    source: "google_sheets",
    supported: true,
    state: "off",
    message: "",
    results: RESULTS,
    google_sheets: null,
    hubspot: null,
    ...over,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function stub(routes: Record<string, (body?: unknown) => Response>) {
  const calls: Array<{ key: string; body?: unknown }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input).replace("/client/v1", "")}`;
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      calls.push({ key, body });
      const handler = routes[key];
      if (!handler) throw new Error(`unexpected fetch ${key}`);
      return handler(body);
    }),
  );
  return calls;
}

function renderCard() {
  return render(
    <MemoryRouter>
      <I18nProvider>
        <ClientAuthProvider>
          <SyncCard laneId="main" laneName="Naga leads" onClose={() => {}} onChanged={async () => {}} />
        </ClientAuthProvider>
      </I18nProvider>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("SyncCard", () => {
  it("saves the columns the client names, one by one, and nothing else", async () => {
    const calls = stub({
      "GET /lanes/main/sync": () => json(sync()),
      "PUT /lanes/main/sync": () => json(sync({ state: "configured", google_sheets: { columns: { status: "Statut" } } })),
    });
    renderCard();
    const status = await screen.findByLabelText("Status");
    await userEvent.type(status, "Statut");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(calls.some((c) => c.key === "PUT /lanes/main/sync")).toBe(true));
    expect(calls.find((c) => c.key === "PUT /lanes/main/sync")?.body).toEqual({
      expected_version: 3,
      google_sheets: { columns: { status: "Statut" }, propagate_opt_out: false },
    });
    expect(await screen.findByText(/^Saved\.$/)).toBeInTheDocument();
  });

  it("refuses to save nothing, and a next action without its due date", async () => {
    const calls = stub({ "GET /lanes/main/sync": () => json(sync()) });
    renderCard();
    await screen.findByLabelText("Status");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
    expect(await screen.findByText(/at least one result/i)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Next action"), "Action");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
    expect(await screen.findByText(/both its type and its due date/i)).toBeInTheDocument();
    expect(calls.filter((c) => c.key.startsWith("PUT"))).toHaveLength(0);
  });

  it("offers no way to switch the copy off: write-back is permanent", async () => {
    stub({
      "GET /lanes/main/sync": () => json(sync({ state: "configured", google_sheets: { columns: { status: "Statut" } } })),
    });
    renderCard();
    await screen.findByRole("button", { name: /^save$/i });
    expect(screen.queryByRole("button", { name: /stop copying|arr.ter/i })).toBeNull();
  });

  it("asks HubSpot clients for property names only, never for a status column or the meaning of statuses", async () => {
    const calls = stub({
      "GET /lanes/main/sync": () =>
        json(
          sync({
            source: "hubspot",
            hubspot: { status: false, properties: {} },
          }),
        ),
      "PUT /lanes/main/sync": () => json(sync({ source: "hubspot", state: "configured", hubspot: { status: false, properties: { last_contacted_at: "revansa_last" } } })),
    });
    renderCard();
    await userEvent.type(await screen.findByLabelText("Date of the last message sent"), "revansa_last");
    expect(screen.queryByLabelText("Status")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(calls.find((c) => c.key.startsWith("PUT"))?.body).toEqual({
      expected_version: 3,
      hubspot: {
        status: false,
        properties: { last_contacted_at: "revansa_last" },
        propagate_opt_out: false,
      },
    }));
  });

  it("says plainly that a source cannot receive results", async () => {
    stub({ "GET /lanes/main/sync": () => json(sync({ supported: false, source: "other" })) });
    renderCard();
    expect(await screen.findByText(/cannot receive results/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^save$/i })).toBeNull();
  });
});
