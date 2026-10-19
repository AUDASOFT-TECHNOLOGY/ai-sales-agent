import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nProvider } from "../../i18n";
import { HubSpotAccessCard, hubspotReturnOf } from "./HubSpotAccessCard";

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

function access(over: Record<string, unknown> = {}) {
  return { state: "not_connected", account: null, message: "", ...over };
}

function stub(routes: Record<string, () => Response>) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input).replace("/client/v1", "")}`;
      calls.push(key);
      const handler = routes[key];
      if (!handler) throw new Error(`unexpected fetch ${key}`);
      return handler();
    }),
  );
  return calls;
}

function renderCard(go?: (url: string) => void) {
  return render(
    <I18nProvider>
      <HubSpotAccessCard go={go} />
    </I18nProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("HubSpotAccessCard", () => {
  it("sends the browser to HubSpot when the client clicks Connect, and asks for nothing else", async () => {
    const go = vi.fn();
    const calls = stub({
      "GET /hubspot-access": () => json(access()),
      "POST /hubspot-access/start": () => json({ authorize_url: "https://app.example/oauth/authorize?state=s" }),
    });
    renderCard(go);
    await userEvent.click(await screen.findByRole("button", { name: /connect hubspot/i }));
    await waitFor(() => expect(go).toHaveBeenCalledWith("https://app.example/oauth/authorize?state=s"));
    expect(calls).toEqual(["GET /hubspot-access", "POST /hubspot-access/start"]);
  });

  it("shows the client's own HubSpot account once connected, and can disconnect", async () => {
    stub({
      "GET /hubspot-access": () => json(access({ state: "connected", account: "ada@naga.example (portal 4242)" })),
      "POST /hubspot-access/disconnect": () => json(access()),
    });
    renderCard();
    expect(await screen.findByText(/portal 4242/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /disconnect hubspot/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /connect hubspot/i })).toBeInTheDocument());
  });

  it("offers to connect again when HubSpot revoked the authorisation", async () => {
    stub({ "GET /hubspot-access": () => json(access({ state: "reauth_required", account: "x (portal 1)" })) });
    renderCard();
    expect(await screen.findByRole("button", { name: /connect again/i })).toBeInTheDocument();
  });

  it("explains that Revansa is not set up yet instead of offering a dead button", async () => {
    stub({ "GET /hubspot-access": () => json(access({ state: "unavailable" })) });
    renderCard();
    expect(await screen.findByText(/finish setting it up/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /connect hubspot/i })).toBeNull();
  });

  it("reads only the known return outcomes", () => {
    expect(hubspotReturnOf("?hubspot=connected")).toBe("connected");
    expect(hubspotReturnOf("?hubspot=<script>")).toBeNull();
    expect(hubspotReturnOf("")).toBeNull();
  });
});
