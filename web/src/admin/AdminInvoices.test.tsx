import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { AdminAuthProvider } from "./AdminAuthContext";
import { AdminRoute } from "./AdminRoute";
import { AdminShell } from "./AdminShell";
import { AdminBillingPage } from "./pages/AdminBillingPage";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

interface TestInvoice {
  invoice_id: number;
  number: string;
  client_id: string;
  period: string;
  issued_on?: string;
  currency: string;
  amount: string;
  status: string;
  lines: unknown[];
  created_at: string;
  created_by: string;
  status_changed_at: string | null;
  status_changed_by: string | null;
  payment_origin?: string | null;
  payment_transaction_id?: string | null;
  payment_event_id?: string | null;
  amount_after_discount?: string | null;
  discount_amount?: string | null;
  free_amount?: string | null;
  discount_percent?: number;
  overdue?: boolean;
  due_at?: string;
}

function invoice(over: Partial<TestInvoice> = {}): TestInvoice {
  return {
    invoice_id: 1,
    number: "INV-2026-04-alpha",
    client_id: "alpha",
    period: "2026-04-20",
    issued_on: "2026-04-20",
    currency: "USD",
    amount: "30.00",
    status: "PENDING",
    lines: [
      {
        plan_id: "pro",
        plan_name: "Pro",
        monthly_price: "30.00",
        currency: "USD",
        included_credits: 1000,
        daily_quota: 50,
        amount: "30.00",
      },
    ],
    created_at: "2026-05-01T09:00:00Z",
    created_by: "root",
    status_changed_at: null,
    status_changed_by: null,
    ...over,
  };
}

const CLIENTS = [
  { client_id: "alpha", display_name: "Alpha", status: "active" },
  { client_id: "beta", display_name: "Beta", status: "active" },
];

interface World {
  role?: string;
  invoices?: TestInvoice[];
  generate?: () => Response;
  settle?: () => Response;
}

function stubFetch({ role = "owner", invoices = [invoice()], generate, settle }: World = {}) {
  let list = invoices;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    if (url.endsWith("/auth/me")) return jsonResponse(200, { username: "root", role, client_ids: [] });
    if (url.endsWith("/admin/v1/plans")) return jsonResponse(200, { plans: [] });
    if (url.endsWith("/admin/v1/clients")) return jsonResponse(200, CLIENTS);
    if (url.endsWith("/billing/invoices/generate") && method === "POST") {
      if (generate) return generate();
      const created = invoice({ invoice_id: 9, number: "INV-2026-03-20-alpha", period: "2026-03-20", issued_on: "2026-03-20" });
      list = [...list, created];
      return jsonResponse(200, { as_of: "2026-05-01T00:00:00Z", created: [created], already_invoiced: 1, skipped: [{ client_id: "beta", reason: "annual plan: not invoiced automatically yet" }] });
    }
    const settleMatch = url.match(/\/billing\/invoices\/(\d+)\/status$/);
    if (settleMatch && method === "PUT") {
      if (settle) return settle();
      const status = (JSON.parse(String(init?.body)) as { status: string }).status;
      const settled = { ...list.find((i) => i.invoice_id === Number(settleMatch[1]))!, status, status_changed_at: "2026-05-02T10:00:00Z", status_changed_by: "root" };
      list = list.map((i) => (i.invoice_id === settled.invoice_id ? settled : i));
      return jsonResponse(200, settled);
    }
    if (url.includes("/billing/invoices") && method === "GET") {
      const query = new URL(url, "http://x").searchParams;
      const filtered = list.filter(
        (i) =>
          (!query.get("client_id") || i.client_id === query.get("client_id")) &&
          (!query.get("period_from") || i.period >= query.get("period_from")!) &&
          (!query.get("period_to") || i.period <= query.get("period_to")!) &&
          (!query.get("status") || i.status === query.get("status")),
      );
      return jsonResponse(200, { invoices: filtered });
    }
    throw new Error(`unexpected fetch ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function renderInvoices() {
  render(
    <I18nProvider>
      <MemoryRouter initialEntries={["/admin/billing"]}>
        <AdminAuthProvider>
          <Routes>
            <Route
              path="/admin"
              element={
                <AdminRoute>
                  <AdminShell />
                </AdminRoute>
              }
            >
              <Route path="billing" element={<AdminBillingPage />} />
            </Route>
          </Routes>
        </AdminAuthProvider>
      </MemoryRouter>
    </I18nProvider>,
  );
  const user = userEvent.setup();
  await user.click(await screen.findByRole("radio", { name: "Invoices" }));
  return user;
}

function calls(fetchMock: ReturnType<typeof stubFetch>, part: string, method = "GET") {
  return fetchMock.mock.calls.filter(([input, init]) => String(input).includes(part) && ((init as RequestInit | undefined)?.method ?? "GET") === method);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Billing: invoices", () => {
  it("lists the real invoices with client, period, amount and status", async () => {
    stubFetch({ invoices: [invoice(), invoice({ invoice_id: 2, client_id: "beta", number: "INV-2026-04-beta", status: "PAID", amount: "40.00", currency: "EUR" })] });
    await renderInvoices();
    const row = (await screen.findByRole("rowheader", { name: "INV-2026-04-alpha" })).closest("tr") as HTMLElement;
    expect(within(row).getByText("Alpha")).toBeInTheDocument();
    expect(within(row).getByText("2026-04-20")).toBeInTheDocument();
    expect(within(row).getByText("30.00 USD")).toBeInTheDocument();
    expect(within(row).getByText("Pending")).toBeInTheDocument();
    const paid = screen.getByRole("rowheader", { name: "INV-2026-04-beta" }).closest("tr") as HTMLElement;
    expect(within(paid).getByText("Paid")).toBeInTheDocument();
    expect(within(paid).getByText("40.00 EUR")).toBeInTheDocument();
  });

  it("filters by client, period and status through the API", async () => {
    const fetchMock = stubFetch({
      invoices: [
        invoice(),
        invoice({ invoice_id: 2, client_id: "beta", number: "INV-2026-04-beta" }),
        invoice({ invoice_id: 3, number: "INV-2026-03-alpha", period: "2026-03", status: "PAID" }),
      ],
    });
    const user = await renderInvoices();
    await screen.findByRole("rowheader", { name: "INV-2026-04-alpha" });
    await user.selectOptions(screen.getByLabelText("Client"), "beta");
    await waitFor(() => expect(screen.queryByRole("rowheader", { name: "INV-2026-04-alpha" })).toBeNull());
    expect(screen.getByRole("rowheader", { name: "INV-2026-04-beta" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Client"), "");
    await user.selectOptions(screen.getByLabelText("Status"), "PAID");
    await waitFor(() => expect(screen.queryByRole("rowheader", { name: "INV-2026-04-alpha" })).toBeNull());
    expect(screen.getByRole("rowheader", { name: "INV-2026-03-alpha" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Status"), "");
    await user.type(screen.getByLabelText("From month"), "2026-04");
    await waitFor(() => expect(screen.queryByRole("rowheader", { name: "INV-2026-03-alpha" })).toBeNull());
    const queries = calls(fetchMock, "/billing/invoices?").map(([input]) => new URL(String(input), "http://x").searchParams.toString());
    expect(queries).toContain("client_id=beta");
    expect(queries).toContain("status=PAID");
    expect(queries).toContain("period_from=2026-04");
  });

  it("shows the plan terms an invoice was built from", async () => {
    stubFetch();
    const user = await renderInvoices();
    await user.click(await screen.findByRole("button", { name: "Details of INV-2026-04-alpha" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Pro")).toBeInTheDocument();
    expect(within(dialog).getByText("Plan price")).toBeInTheDocument();
    expect(within(dialog).getByText("1000")).toBeInTheDocument();
    expect(within(dialog).getByText("50")).toBeInTheDocument();
    expect(within(dialog).getByText(/Total/)).toHaveTextContent("30.00 USD");
  });

  it("lets an owner mark a pending invoice PAID, and then offers nothing more for it", async () => {
    const fetchMock = stubFetch();
    const user = await renderInvoices();
    await user.click(await screen.findByRole("button", { name: "Mark INV-2026-04-alpha paid" }));
    const row = (await screen.findByRole("rowheader", { name: "INV-2026-04-alpha" })).closest("tr") as HTMLElement;
    await waitFor(() => expect(within(row).getByText("Paid")).toBeInTheDocument());
    expect(within(row).queryByRole("button", { name: /Mark/ })).toBeNull();
    expect(JSON.parse(String(calls(fetchMock, "/status", "PUT")[0][1]?.body))).toEqual({ status: "PAID" });
  });

  it("lets an owner mark a pending invoice FOR FREE", async () => {
    const fetchMock = stubFetch();
    const user = await renderInvoices();
    await user.click(await screen.findByRole("button", { name: "Mark INV-2026-04-alpha for free" }));
    const row = (await screen.findByRole("rowheader", { name: "INV-2026-04-alpha" })).closest("tr") as HTMLElement;
    await waitFor(() => expect(within(row).getByText("For free")).toBeInTheDocument());
    expect(JSON.parse(String(calls(fetchMock, "/status", "PUT")[0][1]?.body))).toEqual({ status: "FOR_FREE" });
  });

  it("lets an owner apply a 100% discount, shown as its own status and not as a payment", async () => {
    const fetchMock = stubFetch({
      settle: () =>
        jsonResponse(200, invoice({ status: "DISCOUNTED_100", payment_origin: "manual", amount_after_discount: "0.00", discount_amount: "49.00", free_amount: "49.00", discount_percent: 100 })),
    });
    const user = await renderInvoices();
    await user.click(await screen.findByRole("button", { name: "Apply a 100% discount to INV-2026-04-alpha" }));
    const row = (await screen.findByRole("rowheader", { name: "INV-2026-04-alpha" })).closest("tr") as HTMLElement;
    await waitFor(() => expect(within(row).getByText("Discounted 100%")).toBeInTheDocument());
    expect(within(row).queryByText("Manual")).toBeNull(); // a discount has no payment origin
    expect(within(row).queryByRole("button", { name: /Apply a 100%|Mark/ })).toBeNull();
    expect(JSON.parse(String(calls(fetchMock, "/status", "PUT")[0][1]?.body))).toEqual({ status: "DISCOUNTED_100" });
  });

  it("shows the payment origin and does not offer 'Mark PAID' for an invoice paid electronically", async () => {
    stubFetch({
      invoices: [
        invoice({ status: "PAID", payment_origin: "electronic", payment_transaction_id: "txn_1", payment_event_id: "evt_1" }),
        invoice({ invoice_id: 2, client_id: "beta", number: "INV-2026-04-beta", status: "PAID", payment_origin: "manual" }),
      ],
    });
    const user = await renderInvoices();
    const row = (await screen.findByRole("rowheader", { name: "INV-2026-04-alpha" })).closest("tr") as HTMLElement;
    expect(within(row).getByText("Electronic")).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: /Mark/ })).toBeNull();
    const manual = screen.getByRole("rowheader", { name: "INV-2026-04-beta" }).closest("tr") as HTMLElement;
    expect(within(manual).getByText("Manual")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Details of INV-2026-04-alpha" }));
    expect(await screen.findByText(/transaction txn_1, event evt_1/)).toBeInTheDocument();
  });

  it("shows Failed and Overdue, and still lets an owner settle a failed invoice by hand", async () => {
    stubFetch({ invoices: [invoice({ status: "FAILED", overdue: true, due_at: "2026-05-16T00:00:00Z" })] });
    await renderInvoices();
    const row = (await screen.findByRole("rowheader", { name: "INV-2026-04-alpha" })).closest("tr") as HTMLElement;
    expect(within(row).getByText("Failed")).toBeInTheDocument();
    expect(within(row).getByText("Overdue")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Mark INV-2026-04-alpha paid" })).toBeInTheDocument();
  });

  it("shows the server's refusal when an invoice cannot be settled", async () => {
    stubFetch({ settle: () => jsonResponse(409, { detail: "only a pending invoice can be marked PAID or FOR_FREE" }) });
    const user = await renderInvoices();
    await user.click(await screen.findByRole("button", { name: "Mark INV-2026-04-alpha paid" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("only a pending invoice");
    const row = screen.getByRole("rowheader", { name: "INV-2026-04-alpha" }).closest("tr") as HTMLElement;
    expect(within(row).getByText("Pending")).toBeInTheDocument();
  });

  it("issues the invoices due now and reports what was created, kept and skipped", async () => {
    const fetchMock = stubFetch();
    const user = await renderInvoices();
    const issue = await screen.findByRole("button", { name: "Issue due invoices" });
    expect(issue).toBeEnabled();
    await user.click(issue);
    const status = await screen.findByText(/1 invoice\(s\) issued/);
    expect(status).toHaveTextContent("1 already invoiced (left as they were)");
    expect(status).toHaveTextContent("beta (annual plan: not invoiced automatically yet)");
    expect(await screen.findByRole("rowheader", { name: "INV-2026-03-20-alpha" })).toBeInTheDocument();
    expect(JSON.parse(String(calls(fetchMock, "/generate", "POST")[0][1]?.body))).toEqual({});
  });

  it("shows the server's refusal to issue the invoices", async () => {
    stubFetch({ generate: () => jsonResponse(503, { detail: "the invoices are unavailable" }) });
    const user = await renderInvoices();
    await user.click(await screen.findByRole("button", { name: "Issue due invoices" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("the invoices are unavailable");
  });

  it("is read-only for anyone but an owner", async () => {
    stubFetch({ role: "viewer" });
    await renderInvoices();
    expect(await screen.findByRole("rowheader", { name: "INV-2026-04-alpha" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Issue due invoices" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Mark/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Details of INV-2026-04-alpha" })).toBeInTheDocument();
  });

  it("says when there is no invoice instead of inventing one", async () => {
    stubFetch({ invoices: [] });
    await renderInvoices();
    expect(await screen.findByText(/No invoices match/)).toBeInTheDocument();
  });

  it("says so when billing is unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) return jsonResponse(200, { username: "root", role: "owner", client_ids: [] });
        if (url.endsWith("/admin/v1/plans")) return jsonResponse(200, { plans: [] });
        if (url.endsWith("/admin/v1/clients")) return jsonResponse(200, CLIENTS);
        return jsonResponse(503, { detail: "billing needs the database (set SALES_AGENT_DATABASE_URL)" });
      }),
    );
    await renderInvoices();
    expect(await screen.findByRole("alert")).toHaveTextContent("billing needs the database");
  });
});
