import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../../i18n";
import { ClientDashboardPage } from "./ClientDashboardPage";

const logout = vi.fn(async () => undefined);
vi.mock("../ClientAuthContext", () => ({ useClientAuth: () => ({ logout }) }));

const m = (value: number) => ({ value, availability: "measured" });
const NT = { value: null, availability: "not_tracked", reason: "no conversion event is recorded" };

function payload(over: Record<string, unknown> = {}) {
  return {
    generated_at: "2026-05-01T09:30:00+00:00",
    period: { from: "2026-03-01", to: "2026-05-01", months: ["2026-03"], granularity: "day" },
    agent: { availability: "measured", state: "running", active: true, ready: true, integrations: [] },
    activity: {
      prospects_total: m(5), prospects_contacted: m(4), conversations: m(4), messages_sent: m(4), replies_received: m(2),
      prospects_replied: m(2), reply_rate_percent: m(50), needs_human: m(1), with_errors: m(2), conversions: NT,
      payments: { declared: m(3), confirmed: m(1), awaiting_confirmation: m(2) },
      by_status: { new: 5 }, by_circuit: {}, by_product: { P1: 3 }, by_channel: { email: 5 }, by_source: {}, messages_by_channel: {},
    },
    human_requests: m(1),
    incidents: { open: m(0), by_severity: {}, by_kind: {}, sync_failures: m(0), connection_problems: m(0), items: [] },
    usage: { availability: "measured", state: "ok", daily_used: 3, daily_limit: 10, month_used: 8, message: "", action: "" },
    billing: {
      availability: "measured",
      subscription: { status: "active", plan_name: "Pro", monthly_price: "30.00", currency: "USD", next_billing_at: "2026-06-01T00:00:00Z" },
      invoices: { issued: m(2), paid: m(1), unpaid: m(1), overdue: m(0), recent: [
        { number: "INV-1", period: "2026-04", currency: "USD", amount: "30.00", amount_after_discount: null, status: "PENDING", collected: false, overdue: false },
      ] },
    },
    series: {
      activity: { availability: "measured", granularity: "day", points: [{ period: "2026-04-20", sent: 4, received: 2 }] },
      performance: { availability: "measured", points: [{ period: "2026-04", prospects_contacted: m(4), prospects_replied: m(2), reply_rate_percent: m(50), conversions: NT }] },
      incidents: { availability: "measured", points: [] },
    },
    ...over,
  };
}

function renderPage() {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <ClientDashboardPage />
      </MemoryRouter>
    </I18nProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

function serve(response: () => Response | Promise<Response>) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => (calls.push(url), response())));
  return calls;
}

describe("ClientDashboardPage", () => {
  it("shows the client's activity, results, payments and invoices, without any client id", async () => {
    window.localStorage.setItem("revansa.locale", "en");
    const calls = serve(() => new Response(JSON.stringify(payload())));
    renderPage();
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(await screen.findByText("Prospects handled")).toBeInTheDocument();
    expect(screen.getByText("Declared by buyers")).toBeInTheDocument();
    expect(screen.getByText("INV-1")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Messages sent and received/ })).toBeInTheDocument();
    expect(calls[0]).toMatch(/^\/client\/v1\/dashboard\?from=\d{4}-\d{2}&to=\d{4}-\d{2}$/);
    expect(calls[0]).not.toContain("client_id");
  });

  it("shows conversions as not tracked, never as zero", async () => {
    window.localStorage.setItem("revansa.locale", "en");
    serve(() => new Response(JSON.stringify(payload())));
    renderPage();
    const card = (await screen.findByText("Conversions")).closest(".dv-kpi") as HTMLElement;
    expect(card).toHaveTextContent("—");
    expect(within(card).getByText("Not tracked")).toBeInTheDocument();
  });

  it("speaks French", async () => {
    window.localStorage.setItem("revansa.locale", "fr");
    serve(() => new Response(JSON.stringify(payload())));
    renderPage();
    expect(await screen.findByRole("heading", { name: "Tableau de bord" })).toBeInTheDocument();
    expect(await screen.findByText("Prospects traités")).toBeInTheDocument();
    expect(screen.getAllByText("Non suivi").length).toBeGreaterThan(0);
  });

  it("shows a loading state, then an error with a retry", async () => {
    window.localStorage.setItem("revansa.locale", "en");
    serve(() => new Response("{}", { status: 500 }));
    renderPage();
    expect(screen.getByRole("status")).toHaveTextContent("Loading your dashboard");
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be loaded");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("signs out on an expired session", async () => {
    window.localStorage.setItem("revansa.locale", "en");
    serve(() => new Response("{}", { status: 401 }));
    renderPage();
    await waitFor(() => expect(logout).toHaveBeenCalled());
  });

  it("says when a block is not available instead of inventing it", async () => {
    window.localStorage.setItem("revansa.locale", "en");
    serve(() =>
      new Response(
        JSON.stringify(
          payload({
            usage: { availability: "not_tracked", reason: "x" },
            billing: { availability: "not_tracked", reason: "x" },
            series: {
              activity: { availability: "not_tracked", reason: "this figure needs the database", points: [] },
              performance: { availability: "measured", points: [] },
              incidents: { availability: "measured", points: [] },
            },
          }),
        ),
      ),
    );
    renderPage();
    expect(await screen.findByText(/Not tracked: this figure needs the database/)).toBeInTheDocument();
  });
});
