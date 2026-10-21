import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AdminDashboardPage } from "./AdminDashboardPage";

const m = (value: number) => ({ value, availability: "measured" });

function dashboard(over: Record<string, unknown> = {}) {
  return {
    generated_at: "2026-05-01T09:30:00+00:00",
    period: { from: "2026-03-01", to: "2026-04-30", months: ["2026-03", "2026-04"], granularity: "day" },
    client_id: null,
    scope: "all",
    clients: { total: m(2), active: m(2), inactive: m(0) },
    llm_models: { configured: m(1), models: ["gpt-x"] },
    agents: { total: m(2), active: m(2) },
    clients_list: [],
    activity: {
      prospects_total: m(12),
      prospects_contacted: m(14),
      conversations: m(14),
      messages_sent: m(14),
      replies_received: m(7),
      prospects_replied: m(7),
      reply_rate_percent: m(50),
      needs_human: m(2),
      with_errors: m(4),
      conversions_declared: m(3),
      conversions_confirmed: m(1),
      conversion_rate_declared_percent: m(75),
      conversion_rate_confirmed_percent: m(25),
      payments: { declared: m(0), confirmed: m(0), awaiting_confirmation: m(0) },
      by_status: {}, by_circuit: {}, by_product: {}, by_channel: {}, by_source: {}, messages_by_channel: {},
    },
    finance: {
      availability: "measured",
      totals: [
        { currency: "USD", billed: "105.00", after_discount: "90.00", collected: "30.00", free: "45.00", discounted: "15.00",
          pending: "0.00", overdue: "30.00", invoices_issued: 4, invoices_paid: 1, invoices_free: 1, invoices_discounted: 1,
          invoices_pending: 0, invoices_overdue: 1 },
      ],
      timeline: [{ period: "2026-03", currency: "USD", billed: "60.00", collected: "30.00" }],
      subscriptions: [{ period: "2026-03", active: 2, inactive: 0 }],
    },
    costs: {
      availability: "estimated",
      providers: [
        { provider: "openai", basis: "measured", availability: "measured", totals: { USD: "0.007500" }, complete: true },
        { provider: "meta_whatsapp", basis: "unavailable", availability: "not_tracked", totals: {}, complete: false },
      ],
      series: [{ period: "2026-03", by_provider: [{ provider: "openai", basis: "measured", availability: "measured", totals: { USD: "0.007500" } }], totals: { USD: "0.007500" }, complete: true }],
    },
    profitability: { status: "partial", reason: "some costs are unavailable", margin: {}, revenue: { USD: "30.00" }, costs_known: { USD: "0.007500" } },
    usage: { availability: "measured", clients: [] },
    reliability: {
      open: m(1), by_severity: {}, by_kind: {}, sync_failures: m(1), connection_problems: m(1), items: [],
      failed_actions: m(4), health: { degraded_clients: m(0), clients: [] },
    },
    integrations: { ready: m(2), not_ready: m(0), clients: [] },
    series: {
      activity: { availability: "measured", granularity: "day", points: [{ period: "2026-04-20", sent: 14, received: 7 }] },
      performance: { availability: "measured", points: [{ period: "2026-04", prospects_contacted: m(14), prospects_replied: m(7), reply_rate_percent: m(50), conversions_declared: m(3), conversions_confirmed: m(1), conversion_rate_declared_percent: m(75), conversion_rate_confirmed_percent: m(25) }] },
      finance: { availability: "measured", timeline: [{ period: "2026-03", currency: "USD", billed: "60.00", collected: "30.00" }] },
      subscriptions: { availability: "measured", points: [{ period: "2026-03", active: 2, inactive: 0 }] },
      costs: { availability: "estimated", points: [{ period: "2026-03", by_provider: [{ provider: "openai", basis: "measured", availability: "measured", totals: { USD: "0.007500" } }], totals: { USD: "0.007500" }, complete: true }] },
      reliability: { availability: "measured", points: [{ period: "2026-04-20", incidents: 1 }] },
    },
    ...over,
  };
}

const CLIENTS = [
  { client_id: "alpha", display_name: "Alpha", status: "active", llm_model: "gpt-x", providers: {} },
  { client_id: "beta", display_name: "Beta", status: "active", llm_model: "gpt-x", providers: {} },
];
const AGENTS = [
  { agent_id: "alpha", client_id: "alpha", status: "active" },
  { agent_id: "beta", client_id: "beta", status: "disabled" },
];

function serve(dash: (url: string) => Response | Promise<Response>) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(url);
      if (url.endsWith("/clients")) return new Response(JSON.stringify(CLIENTS));
      if (url.endsWith("/agents")) return new Response(JSON.stringify(AGENTS));
      if (url.includes("/dashboard")) return dash(url);
      return new Response("{}", { status: 404 });
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("AdminDashboardPage", () => {
  it("keeps the existing indicators and adds the six chart families", async () => {
    serve(() => new Response(JSON.stringify(dashboard())));
    render(<AdminDashboardPage />);
    for (const label of ["Clients", "LLM Models configured", "Active Agents", "Total Revenue", "Prospects contacted", "Conversations", "Conversions declared", "Conversions confirmed", "Revenue", "LLM cost", "WhatsApp cost"]) {
      expect(await screen.findByText(label)).toBeInTheDocument();
    }
    for (const heading of ["Commercial activity", "Financial evolution", "Subscriptions", "Technical costs by provider", "Reliability"]) {
      expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
    }
    expect(await screen.findByRole("img", { name: /Agent performance: reply rate/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Billed vs collected \(USD\)/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Costs by provider \(USD\)/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Incidents raised/ })).toBeInTheDocument();
  });

  it("tells billed, collected, free and discounted apart", async () => {
    serve(() => new Response(JSON.stringify(dashboard())));
    render(<AdminDashboardPage />);
    const table = await screen.findByRole("table", { name: "Invoice totals by currency" });
    const row = within(table).getByRole("row", { name: /USD/ });
    expect(row).toHaveTextContent("$105.00");
    expect(row).toHaveTextContent("$30.00");
    expect(row).toHaveTextContent("$45.00");
    expect(row).toHaveTextContent("$15.00");
    for (const col of ["Billed", "Collected", "Free", "Discounted"]) {
      expect(within(table).getByRole("columnheader", { name: col })).toBeInTheDocument();
    }
  });

  it("never shows a figure that is not tracked as zero", async () => {
    serve(() => new Response(JSON.stringify(dashboard())));
    render(<AdminDashboardPage />);
    const declared = (await screen.findByText("Conversions declared")).closest(".admin-stat-card") as HTMLElement;
    const confirmed = (await screen.findByText("Conversions confirmed")).closest(".admin-stat-card") as HTMLElement;
    await waitFor(() => expect(declared).toHaveTextContent("3"));
    expect(declared).toHaveTextContent("75.0% of prospects contacted");
    expect(confirmed).toHaveTextContent("1");
    expect(confirmed).toHaveTextContent("payment confirmed by the client");
    const wa = screen.getByText("WhatsApp cost").closest(".admin-stat-card") as HTMLElement;
    expect(wa).toHaveTextContent("—");
    expect(await screen.findByText(/Profitability/)).toBeInTheDocument();
    expect(screen.getByText(/some costs are unavailable/)).toBeInTheDocument();
  });

  it("asks for the selected client and period", async () => {
    const calls = serve(() => new Response(JSON.stringify(dashboard())));
    render(<AdminDashboardPage />);
    await screen.findByRole("heading", { name: "Reliability" });
    await userEvent.selectOptions(screen.getByLabelText("Client"), "beta");
    await waitFor(() => expect(calls.some((u) => u.includes("client_id=beta"))).toBe(true));
    expect(calls.find((u) => u.includes("/dashboard"))).toMatch(/from=\d{4}-\d{2}&to=\d{4}-\d{2}/);
  });

  it("keeps the base indicators when the dashboard endpoint fails", async () => {
    serve(() => new Response(JSON.stringify({ detail: "boom" }), { status: 500 }));
    render(<AdminDashboardPage />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Active Agents")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Reliability" })).toBeNull();
  });

  it("shows a loading state first", () => {
    serve(() => new Promise<Response>(() => undefined));
    render(<AdminDashboardPage />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading dashboard");
  });
});
