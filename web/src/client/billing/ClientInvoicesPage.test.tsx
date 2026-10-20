import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nProvider } from "../../i18n";
import { ClientInvoicesPage } from "./ClientInvoicesPage";

const logout = vi.fn(async () => undefined);
vi.mock("../ClientAuthContext", () => ({ useClientAuth: () => ({ logout }) }));

function invoice(over: Record<string, unknown> = {}) {
  return {
    invoice_id: 1,
    number: "INV-2026-04-alpha",
    period: "2026-04",
    currency: "USD",
    amount: "30.00",
    amount_after_discount: null,
    discount_amount: null,
    free_amount: null,
    status: "PENDING",
    collected: false,
    due_at: "2026-05-16T00:00:00Z",
    overdue: false,
    created_at: "2026-05-01T00:00:00Z",
    status_changed_at: null,
    ...over,
  };
}

const SUBSCRIPTION = {
  status: "active",
  plan: {
    plan_id: "pro",
    name: "Pro",
    monthly_price: "30.00",
    currency: "USD",
    billing_period: "monthly",
    included_credits: 1000,
    daily_quota: 50,
    since: "2026-03-01T00:00:00Z",
  },
  next_billing_at: "2026-06-01T00:00:00Z",
  unpaid_invoices: 1,
  overdue_invoices: 0,
  last_payment: { invoice_number: "INV-2026-04-alpha", period: "2026-04", status: "PENDING", collected: false, settled_at: null },
};

function serve(routes: Record<string, { status?: number; body: unknown }>) {
  const calls: Array<{ url: string; method: string }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? "GET" });
      const hit = routes[url];
      if (!hit) return new Response("{}", { status: 404 });
      return new Response(JSON.stringify(hit.body), { status: hit.status ?? 200 });
    }),
  );
  return calls;
}

function show() {
  window.localStorage.setItem("revansa.locale", "en");
  return render(
    <I18nProvider>
      <ClientInvoicesPage />
    </I18nProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
  logout.mockClear();
});

describe("ClientInvoicesPage", () => {
  it("shows the plan, its quota and periodicity, and the invoices", async () => {
    serve({
      "/client/v1/subscription": { body: SUBSCRIPTION },
      "/client/v1/invoices": { body: { invoices: [invoice()] } },
    });
    show();
    expect(await screen.findByText("Pro")).toBeInTheDocument();
    expect(screen.getByText("Monthly")).toBeInTheDocument();
    expect(screen.getByText("50 actions per day")).toBeInTheDocument();
    const row = screen.getByText("INV-2026-04-alpha", { selector: "td" }).closest("tr") as HTMLElement;
    expect(within(row).getByText("Pending")).toBeInTheDocument();
  });

  it("never presents a free invoice or a 100% discount as a payment", async () => {
    serve({
      "/client/v1/subscription": { body: SUBSCRIPTION },
      "/client/v1/invoices": {
        body: {
          invoices: [
            invoice({ invoice_id: 2, number: "INV-2026-03-alpha", status: "FOR_FREE", free_amount: "30.00" }),
            invoice({ invoice_id: 3, number: "INV-2026-02-alpha", status: "DISCOUNTED_100", amount_after_discount: "0.00", discount_amount: "30.00", free_amount: "30.00" }),
          ],
        },
      },
    });
    show();
    expect(await screen.findByText("Free", { selector: ".chip span" })).toBeInTheDocument();
    expect(screen.getByText("100% discount", { selector: ".chip span" })).toBeInTheDocument();
    expect(screen.queryByText("Paid")).toBeNull();
  });

  it("marks an overdue invoice and shows a paid one as paid", async () => {
    serve({
      "/client/v1/subscription": { body: SUBSCRIPTION },
      "/client/v1/invoices": {
        body: {
          invoices: [
            invoice({ invoice_id: 4, number: "INV-2026-03-alpha", overdue: true }),
            invoice({ invoice_id: 5, number: "INV-2026-02-alpha", status: "PAID", collected: true }),
          ],
        },
      },
    });
    show();
    expect(await screen.findByText("Overdue", { selector: ".chip span" })).toBeInTheDocument();
    expect(screen.getByText("Paid", { selector: ".chip span" })).toBeInTheDocument();
  });

  it("opens an invoice's lines and is read only (GET requests only)", async () => {
    const calls = serve({
      "/client/v1/subscription": { body: SUBSCRIPTION },
      "/client/v1/invoices": { body: { invoices: [invoice()] } },
      "/client/v1/invoices/1": {
        body: { ...invoice(), lines: [{ plan_name: "Pro", monthly_price: "30.00", currency: "USD", days: 30, days_in_period: 30, amount: "30.00" }] },
      },
    });
    show();
    await userEvent.click(await screen.findByRole("button", { name: /Details INV-2026-04-alpha/ }));
    expect(await screen.findByText("30 / 30")).toBeInTheDocument();
    expect(calls.every((c) => c.method === "GET")).toBe(true);
    expect(screen.queryByRole("button", { name: /pay|mark|settle/i })).toBeNull();
  });

  it("says there is no invoice yet and no plan, without inventing anything", async () => {
    serve({
      "/client/v1/subscription": {
        body: { status: "no_plan", plan: null, next_billing_at: null, unpaid_invoices: 0, overdue_invoices: 0, last_payment: null },
      },
      "/client/v1/invoices": { body: { invoices: [] } },
    });
    show();
    expect(await screen.findByText(/not on a plan yet/)).toBeInTheDocument();
    expect(screen.getByText(/No invoice yet/)).toBeInTheDocument();
  });

  it("signs the client out when the session ended, and offers a retry when billing is down", async () => {
    serve({ "/client/v1/subscription": { status: 401, body: {} }, "/client/v1/invoices": { status: 401, body: {} } });
    show();
    await waitFor(() => expect(logout).toHaveBeenCalled());
    vi.unstubAllGlobals();
    serve({ "/client/v1/subscription": { status: 503, body: {} }, "/client/v1/invoices": { status: 503, body: {} } });
    show();
    expect(await screen.findAllByRole("alert")).not.toHaveLength(0);
    expect(screen.getAllByRole("button", { name: "Try again" }).length).toBeGreaterThan(0);
  });
});
