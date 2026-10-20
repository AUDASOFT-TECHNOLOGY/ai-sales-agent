import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HubSpotHealthPanel } from "./pages/HubSpotHealthPanel";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const BASE = "/admin/v1/clients/alpha/hubspot";
const health = (over: Record<string, unknown> = {}) => ({
  client_id: "alpha",
  uses_hubspot: true,
  can_resume: true,
  connection: { state: "connected", platform_configured: true, message: "The client's HubSpot account is connected.", action: "none", steps: [], client_path: null },
  statuses: { configured: true, status_property: "hs_lead_status", values: { new: ["NEW"], closed: ["LOST"] }, problem: null },
  writeback: { ledger_available: true, applied: 3, waiting: 1, failed: 0, overruled: 0, problems: [] },
  ...over,
});

function stub(body: unknown) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input)}`;
      calls.push(key);
      if (key === `GET ${BASE}/health`) return json(200, body);
      if (key === `POST ${BASE}/resume-sync`) return json(200, { ran: true, message: "The synchronisation was resumed.", counters: {}, problems: [] });
      if (key === `POST ${BASE}/status-mapping/check`) return json(200, { ok: false, problems: ["status_mapping: bad"] });
      throw new Error(`unexpected ${key}`);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("HubSpotHealthPanel", () => {
  it("shows the mapping and resumes the synchronisation", async () => {
    const calls = stub(health());
    render(<HubSpotHealthPanel clientId="alpha" canEdit draft={{ status_mapping: { NEW: "new" } }} />);
    expect(await screen.findByText(/LOST/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /resume synchronisation/i }));
    expect(await screen.findByText("The synchronisation was resumed.")).toBeTruthy();
    expect(calls).toContain(`POST ${BASE}/resume-sync`);
  });

  it("guides a reconnection when the authorisation expired and offers the server check", async () => {
    stub(
      health({
        can_resume: false,
        connection: { state: "reauth_required", platform_configured: true, message: "HubSpot refused the client's authorisation.", action: "ask_client_to_reconnect", steps: ["Tell the client."], client_path: "/client/connections/sources" },
      }),
    );
    render(<HubSpotHealthPanel clientId="alpha" canEdit draft={{ status_mapping: { NEW: "new" } }} />);
    expect(await screen.findByText("Tell the client.")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /check with the server/i }));
    expect(await screen.findByText("status_mapping: bad")).toBeTruthy();
  });

  it("renders nothing for a client without HubSpot", async () => {
    stub(health({ uses_hubspot: false }));
    const { container } = render(<HubSpotHealthPanel clientId="alpha" canEdit draft={null} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(container.innerHTML).toBe("");
  });
});
