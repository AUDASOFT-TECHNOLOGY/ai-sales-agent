import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { ClientChannelPage } from "./ClientChannelPage";

function channel(email: Record<string, unknown> = {}, over: Record<string, unknown> = {}) {
  return {
    version: 3,
    channel: "email",
    authorized: true,
    options: [{ key: "email", label: "Email" }],
    status: "waiting_platform",
    status_message: "Revansa is setting up this channel for you. There is nothing for you to do.",
    email: { mode: "platform", from_name: "Acme Sales", sender_name_confirmed: true, subject: "Hello", activation: null, ...email },
    telegram: null,
    sender_name_proposal: "Acme Shop",
    updated_at: null,
    updated_by: null,
    ...over,
  };
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

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

function renderPage() {
  render(
    <I18nProvider>
      <MemoryRouter>
        <ClientChannelPage />
      </MemoryRouter>
    </I18nProvider>,
  );
}

const request = (status: string, reason: string | null = null) => ({
  status,
  requested_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-02T10:00:00Z",
  reason,
});

afterEach(() => vi.unstubAllGlobals());

describe("Email activation (client)", () => {
  it("offers to request the activation once the sender name is confirmed, and shows Pending after", async () => {
    const calls = stub({
      "GET /channel": () => json(200, channel()),
      "POST /channel/email-activation": () => json(201, channel({ activation: request("pending") })),
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Request activation" }));
    expect(await screen.findByTestId("activation-status")).toHaveTextContent("Pending");
    expect(calls).toContain("POST /channel/email-activation");
    expect(screen.queryByRole("button", { name: "Request activation" })).not.toBeInTheDocument();
  });

  it("cannot request before the sender name is confirmed", async () => {
    stub({ "GET /channel": () => json(200, channel({ sender_name_confirmed: false, from_name: null })) });
    renderPage();
    expect(await screen.findByRole("button", { name: "Request activation" })).toBeDisabled();
    expect(screen.getByText(/Confirm your sender name above first/)).toBeInTheDocument();
  });

  it.each([
    ["pending", "Pending"],
    ["in_progress", "In progress"],
    ["active", "Active"],
  ])("shows the %s status with its date and no way to ask again", async (status, label) => {
    stub({ "GET /channel": () => json(200, channel({ activation: request(status) })) });
    renderPage();
    expect(await screen.findByTestId("activation-status")).toHaveTextContent(label);
    expect(screen.getByText(/Requested on/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Request/ })).not.toBeInTheDocument();
  });

  it("shows the reason of a rejection and lets the client ask again", async () => {
    stub({ "GET /channel": () => json(200, channel({ activation: request("rejected", "Not on your plan") })) });
    renderPage();
    expect(await screen.findByTestId("activation-status")).toHaveTextContent("Rejected");
    expect(screen.getByText(/Not on your plan/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Request again" })).toBeEnabled();
  });

  it("has no activation for a historical mailbox", async () => {
    stub({ "GET /channel": () => json(200, channel({ mode: "custom" })) });
    renderPage();
    await screen.findByLabelText("Default subject");
    expect(screen.queryByText("Email activation")).not.toBeInTheDocument();
  });
});
