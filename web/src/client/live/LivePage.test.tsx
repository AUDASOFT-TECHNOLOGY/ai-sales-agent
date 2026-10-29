import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../../i18n";
import { LivePage } from "./LivePage";

const logout = vi.fn(async () => undefined);
vi.mock("../ClientAuthContext", () => ({ useClientAuth: () => ({ logout }) }));

const step = (over: Record<string, unknown>) => ({
  id: 1,
  kind: "thinking",
  state: "running",
  started_at: new Date(Date.now() - 4000).toISOString(),
  ended_at: null,
  prospect_id: "p1",
  name: "Ana Martin",
  detail: null,
  ...over,
});

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function stub(live: () => Response, calls: string[] = []) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url.startsWith("/client/v1/live")) return live();
      if (url.startsWith("/client/v1/conversations/")) {
        return json(200, {
          prospect_id: "p1",
          conversation_id: "c1",
          origin: "outbound",
          messages: [{ id: "m1", direction: "inbound", channel: "whatsapp", text: "Bonjour, quel est le prix ?", timestamp: new Date().toISOString(), author: "prospect" }],
        });
      }
      throw new Error(`unexpected fetch ${url}`);
    }),
  );
}

function page() {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <LivePage />
      </MemoryRouter>
    </I18nProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("LivePage", () => {
  it("shows what the agent does now, the person's conversation and a typing line", async () => {
    window.localStorage.setItem("revansa.locale", "en");
    const running = step({});
    stub(() => json(200, { at: new Date().toISOString(), now: [running], recent: [running] }));
    page();
    expect(await screen.findByTestId("live-now")).toHaveTextContent("Preparing a message for Ana Martin");
    expect(screen.getByTestId("live-status")).toHaveTextContent("Your agent is working");
    expect(await screen.findByText("Bonjour, quel est le prix ?")).toBeInTheDocument();
    expect(screen.getByTestId("live-typing")).toHaveTextContent("Writing a message to Ana Martin");
    expect(screen.getByTestId("live-journal")).toBeInTheDocument();
  });

  it("lights the phase being worked on and lists the steps for that person", async () => {
    window.localStorage.setItem("revansa.locale", "en");
    const done = step({ id: 1, kind: "checking", state: "done", ended_at: new Date().toISOString() });
    const running = step({ id: 2, kind: "thinking" });
    stub(() => json(200, { at: new Date().toISOString(), now: [running], recent: [running, done] }));
    page();
    await screen.findByTestId("live-now");
    expect(screen.getByTestId("live-strip").querySelector("[data-state='active']")).toHaveTextContent("Preparing");
    expect(screen.getByText("Checks that the person can be contacted")).toBeInTheDocument();
    expect(screen.getByText("Writes the message")).toBeInTheDocument();
  });

  it("asks before taking over, and sends nothing until the client confirms", async () => {
    window.localStorage.setItem("revansa.locale", "en");
    const running = step({});
    const calls: string[] = [];
    stub(() => json(200, { at: new Date().toISOString(), now: [running], recent: [running] }), calls);
    page();
    await screen.findByTestId("live-now");
    await userEvent.click(await screen.findByRole("button", { name: /Take over/ }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Take over with Ana Martin?");
    expect(calls.some((c) => c.includes("take-over"))).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("says plainly that nothing is in progress", async () => {
    window.localStorage.setItem("revansa.locale", "en");
    stub(() => json(200, { at: new Date().toISOString(), now: [], recent: [] }));
    page();
    expect(await screen.findByTestId("live-idle")).toHaveTextContent("waiting for its next task");
    expect(screen.getByTestId("live-nothread")).toBeInTheDocument();
  });

  it("tells when the connection is lost instead of showing stale news as current", async () => {
    window.localStorage.setItem("revansa.locale", "en");
    stub(() => json(503, { detail: "down" }));
    page();
    await waitFor(() => expect(screen.getByTestId("live-status")).toHaveTextContent("Connection lost"));
  });

  it("signs out when the session is refused", async () => {
    stub(() => json(401, { detail: "no session" }));
    page();
    await waitFor(() => expect(logout).toHaveBeenCalled());
  });
});
