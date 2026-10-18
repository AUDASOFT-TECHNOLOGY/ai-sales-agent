import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { ClientReportingPage } from "./ClientReportingPage";

const logout = vi.fn(async () => undefined);
vi.mock("./ClientAuthContext", () => ({ useClientAuth: () => ({ logout }) }));

type Json = Record<string, unknown>;

const CHANNELS = [
  { key: "email", available: true, reason: null },
  { key: "telegram", available: false, reason: "not_authorized" },
  { key: "whatsapp", available: false, reason: "not_supported" },
];

const SETTINGS = {
  enabled: true,
  channel: "email",
  recipient: "owner@example.com",
  send_time: "09:00",
  timezone: "Africa/Tunis",
  language: "en",
  updated_at: "2026-10-17T12:00:00Z",
};

function state(over: Json = {}) {
  return { settings: null, channels: CHANNELS, languages: ["en", "fr", "ar"], latest: null, ...over };
}

function summary(day: string, over: Json = {}) {
  return {
    day,
    status: "sent",
    channel: "email",
    recipient_hint: "o***@example.com",
    generated_at: "2026-10-17T12:00:00Z",
    sent_at: "2026-10-17T12:00:05Z",
    attempts: 1,
    next_attempt_at: null,
    error_code: null,
    problem: null,
    period_start: "2026-10-16T08:00:00Z",
    period_end: "2026-10-17T08:00:00Z",
    timezone: "Africa/Tunis",
    messages_sent: 4,
    replies_received: 1,
    ...over,
  };
}

function detail(day: string, over: Json = {}) {
  return {
    ...summary(day, over),
    language: "en",
    text: `Daily report ${day}\nMessages sent: 4`,
    figures: {
      prospects: { total: 3, by_status: { new: 3 } },
      activity: {
        messages_sent: 4,
        prospects_contacted: 2,
        new_contacts: 1,
        follow_ups_sent: 2,
        answers_sent: 1,
        replies_received: 1,
        prospects_replied: 1,
      },
      follow_ups: { scheduled: 2, due_next_24h: 1 },
      purchase_intents: { tracked: false },
      appointments: { tracked: false },
      conversions: { tracked: false },
      quota: null,
      actions_required: [{ code: "needs_human", count: 2 }],
    },
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Handler = (init: RequestInit | undefined) => Response | Promise<Response>;

/** Only fetch is simulated (the server is not reachable from a unit test). */
function stub(routes: Record<string, Handler>) {
  const calls: Array<{ key: string; body?: string }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input)}`;
      calls.push({ key, body: init?.body as string | undefined });
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
        <ClientReportingPage />
      </MemoryRouter>
    </I18nProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  logout.mockClear();
});

describe("ClientReportingPage", () => {
  it("says nothing is scheduled, lists the closed channels honestly and never prefills a time zone", async () => {
    stub({
      "GET /client/v1/reporting": () => json(200, state()),
      "GET /client/v1/reports?limit=30": () => json(200, { reports: [] }),
    });
    renderPage();
    expect(await screen.findByTestId("reporting-unconfigured")).toBeInTheDocument();
    const closed = screen.getByTestId("reporting-closed-channels");
    expect(within(closed).getByText(/WhatsApp: not supported for reports/)).toBeInTheDocument();
    expect(within(closed).getByText(/Telegram: not open to your account yet/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Time zone/)).toHaveValue("");
    expect(screen.getByTestId("reporting-empty")).toBeInTheDocument();
  });

  it("saves the settings it was given, with the zone the client typed", async () => {
    const calls = stub({
      "GET /client/v1/reporting": () => json(200, state()),
      "GET /client/v1/reports?limit=30": () => json(200, { reports: [] }),
      "PUT /client/v1/reporting/settings": () => json(200, SETTINGS),
    });
    renderPage();
    await screen.findByTestId("reporting-unconfigured");
    await userEvent.type(screen.getByLabelText(/Email address for the report/), "owner@example.com");
    await userEvent.type(screen.getByLabelText(/Time zone/), "Africa/Tunis");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Your report settings are saved.")).toBeInTheDocument();
    const put = calls.find((c) => c.key.startsWith("PUT"))!;
    expect(JSON.parse(put.body!)).toEqual({
      enabled: true,
      channel: "email",
      recipient: "owner@example.com",
      send_time: "09:00",
      timezone: "Africa/Tunis",
      language: "en",
    });
  });

  it("shows the server's refusal of a bad zone instead of pretending it was saved", async () => {
    stub({
      "GET /client/v1/reporting": () => json(200, state({ settings: SETTINGS })),
      "GET /client/v1/reports?limit=30": () => json(200, { reports: [] }),
      "PUT /client/v1/reporting/settings": () => json(422, { detail: "invalid_timezone" }),
    });
    renderPage();
    await screen.findByLabelText(/Time zone/);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/time zone name is not valid/);
    expect(screen.queryByText("Your report settings are saved.")).not.toBeInTheDocument();
  });

  it("shows each report's real delivery state, with the reason when it was not delivered", async () => {
    stub({
      "GET /client/v1/reporting": () => json(200, state({ settings: SETTINGS })),
      "GET /client/v1/reports?limit=30": () =>
        json(200, {
          reports: [
            summary("2026-10-17"),
            summary("2026-10-16", { status: "failed", sent_at: null, error_code: "send_cap_reached", problem: "x" }),
            summary("2026-10-15", { status: "undeliverable", sent_at: null, error_code: "report_settings_missing" }),
          ],
        }),
    });
    renderPage();
    expect(await screen.findByTestId("report-status-2026-10-17")).toHaveTextContent("Sent");
    expect(screen.getByTestId("report-status-2026-10-16")).toHaveTextContent("Not delivered");
    expect(screen.getByTestId("report-problem-2026-10-16")).toHaveTextContent(/daily email limit is reached/);
    expect(screen.getByTestId("report-status-2026-10-15")).toHaveTextContent("Cannot be delivered");
    expect(screen.getByTestId("report-problem-2026-10-15")).toHaveTextContent(/nothing was sent/);
    // A delivered report offers no "send again"; the others do.
    expect(within(screen.getByTestId("report-2026-10-17")).queryByRole("button", { name: "Send again" })).toBeNull();
    expect(within(screen.getByTestId("report-2026-10-16")).getByRole("button", { name: "Send again" })).toBeInTheDocument();
  });

  it("previews a report with its figures, what is not tracked, and what needs the client", async () => {
    stub({
      "GET /client/v1/reporting": () => json(200, state({ settings: SETTINGS })),
      "GET /client/v1/reports?limit=30": () => json(200, { reports: [summary("2026-10-17")] }),
      "GET /client/v1/reports/2026-10-17": () => json(200, detail("2026-10-17")),
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Preview" }));
    expect(await screen.findByTestId("report-text")).toHaveTextContent("Messages sent: 4");
    expect(screen.getByTestId("report-untracked")).toHaveTextContent(/not tracked/);
    expect(screen.getByTestId("report-untracked")).toHaveTextContent(/Nothing here is a confirmed sale/);
    expect(screen.getByTestId("report-actions")).toHaveTextContent(/prospect\(s\) wait for a person/);
    expect(screen.getByTestId("report-figures")).toHaveTextContent("4");
  });

  it("generates on demand and then shows what the server says about its delivery", async () => {
    let generated = false;
    const calls = stub({
      "GET /client/v1/reporting": () => json(200, state({ settings: SETTINGS })),
      "GET /client/v1/reports?limit=30": () =>
        json(200, { reports: generated ? [summary("2026-10-17", { status: "failed", sent_at: null, error_code: "channel_unavailable" })] : [] }),
      "POST /client/v1/reports/generate": () => {
        generated = true;
        return json(200, detail("2026-10-17", { status: "failed", sent_at: null, error_code: "channel_unavailable" }));
      },
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Generate now" }));
    expect(await screen.findByTestId("report-status-2026-10-17")).toHaveTextContent("Not delivered");
    expect(screen.getByTestId("report-problem-2026-10-17")).toHaveTextContent(/could not be reached/);
    expect(calls.filter((c) => c.key.startsWith("POST"))).toHaveLength(1);
  });

  it("sends again and re-reads the state, even when the server says it was already sent", async () => {
    let sent = false;
    stub({
      "GET /client/v1/reporting": () => json(200, state({ settings: SETTINGS })),
      "GET /client/v1/reports?limit=30": () =>
        json(200, { reports: [sent ? summary("2026-10-16") : summary("2026-10-16", { status: "failed", sent_at: null, error_code: "send_failed" })] }),
      "POST /client/v1/reports/2026-10-16/resend": () => {
        sent = true;
        return json(409, { detail: "this report was already sent or is being sent" });
      },
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Send again" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/already sent/);
    await waitFor(() => expect(screen.getByTestId("report-status-2026-10-16")).toHaveTextContent("Sent"));
  });

  it("signs out on an expired session and reports a store outage plainly", async () => {
    stub({
      "GET /client/v1/reporting": () => json(401, { detail: "session expired or invalid" }),
      "GET /client/v1/reports?limit=30": () => json(401, { detail: "session expired or invalid" }),
    });
    renderPage();
    await waitFor(() => expect(logout).toHaveBeenCalled());
  });

  it("says so when the deployment keeps no reports", async () => {
    stub({
      "GET /client/v1/reporting": () => json(503, { detail: "daily reports are not available on this deployment" }),
      "GET /client/v1/reports?limit=30": () => json(503, { detail: "x" }),
    });
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent(/temporarily unavailable/);
    expect(screen.queryByTestId("report-text")).not.toBeInTheDocument();
  });

  it("offers no save when no channel is open to the account", async () => {
    stub({
      "GET /client/v1/reporting": () =>
        json(200, state({ channels: CHANNELS.map((c) => ({ ...c, available: false, reason: c.reason ?? "not_authorized" })) })),
      "GET /client/v1/reports?limit=30": () => json(200, { reports: [] }),
    });
    renderPage();
    expect(await screen.findByTestId("reporting-no-channel")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});
