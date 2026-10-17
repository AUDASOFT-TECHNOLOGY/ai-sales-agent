import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { AdminAuthProvider } from "./AdminAuthContext";
import { AdminRoute } from "./AdminRoute";
import { AdminShell } from "./AdminShell";
import { AdminBillingPage } from "./pages/AdminBillingPage";
import { AdminClientDetailPage } from "./pages/AdminClientDetailPage";
import { AdminClientsPage } from "./pages/AdminClientsPage";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function plan(over: Record<string, unknown> = {}) {
  return {
    plan_id: "pro",
    name: "Pro",
    monthly_price: "49.90",
    currency: "USD",
    included_credits: 1000,
    daily_quota: 200,
    active: true,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
    updated_by: "root",
    client_count: 2,
    ...over,
  };
}

function assignment(over: Record<string, unknown> = {}) {
  return {
    plan_id: "pro",
    plan_name: "Pro",
    monthly_price: "49.90",
    currency: "USD",
    included_credits: 1000,
    daily_quota: 200,
    started_at: "2026-01-05T09:00:00Z",
    ended_at: null,
    assigned_by: "root",
    ...over,
  };
}

const ALPHA = {
  client_id: "alpha",
  display_name: "Alpha",
  status: "active",
  mode: "approval",
  timezone: "Africa/Tunis",
  daily_action_limit: 20,
  max_attempts: 3,
  opt_out_keywords: [],
  providers: { sheets: "google_sheets", llm: "openai", whatsapp: "whatsapp" },
  sheets: { provider: "google_sheets", spreadsheet_id: "s", prospects_tab: "P", actions_tab: "A", messages_tab: "M", log_tab: "L", credentials_env: "X" },
  llm: { provider: "openai", model: "test-model", api_key_env: "OPENAI_API_KEY" },
  whatsapp: { provider: "whatsapp", api_version: "v21.0", token_env: "T", phone_number_id_env: "P", app_secret_env: "S" },
  brain: { factory: "f", settings_version: 1 },
  schedule: { daily_at: "09:00", days: ["mon"], prepare_every_minutes: 60, dispatch_every_minutes: 5, recover_every_minutes: 15 },
  account: null,
  meta: { status: "not_connected", credentials_stored: false },
};

interface World {
  role?: string;
  plans?: ReturnType<typeof plan>[];
  current?: ReturnType<typeof assignment> | null;
  refuse?: { status: number; detail: string };
}

function stubFetch({ role = "owner", plans = [plan()], current = null, refuse }: World = {}) {
  let list = plans;
  let assigned = current;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
    if (url.endsWith("/auth/me")) return jsonResponse(200, { username: "root", role, client_ids: ["alpha"] });
    if (refuse && method !== "GET") return jsonResponse(refuse.status, { detail: refuse.detail });
    if (url.endsWith("/admin/v1/plans") && method === "GET") return jsonResponse(200, { plans: list });
    if (url.endsWith("/admin/v1/plans") && method === "POST") {
      const created = plan({ plan_id: String(body?.name).toLowerCase(), ...body, monthly_price: String(body?.monthly_price), client_count: 0 });
      list = [...list, created];
      return jsonResponse(201, created);
    }
    if (url.includes("/admin/v1/plans/") && method === "PUT") {
      const id = decodeURIComponent(url.split("/plans/")[1]);
      const updated = plan({ ...list.find((p) => p.plan_id === id), ...body });
      list = list.map((p) => (p.plan_id === id ? updated : p));
      return jsonResponse(200, updated);
    }
    if (url.endsWith("/clients/alpha/plan") && method === "PUT") {
      const chosen = list.find((p) => p.plan_id === body?.plan_id);
      assigned = chosen ? assignment({ plan_id: chosen.plan_id, plan_name: chosen.name }) : null;
      return jsonResponse(200, { client_id: "alpha", current: assigned, history: assigned ? [assigned] : [] });
    }
    if (url.endsWith("/clients/alpha/plan")) {
      return jsonResponse(200, { client_id: "alpha", current: assigned, history: assigned ? [assigned] : [] });
    }
    if (url.endsWith("/settings/llm")) {
      return jsonResponse(200, { read_only: true, restricted: false, providers: [{ provider: "openai", wired: true, allowed_models: ["gpt-4o"] }] });
    }
    if (url.endsWith("/clients/alpha")) return jsonResponse(200, ALPHA);
    if (url.endsWith("/admin/v1/clients") && method === "GET") return jsonResponse(200, [{ client_id: "alpha", display_name: "Alpha", status: "active" }]);
    if (url.endsWith("/admin/v1/clients") && method === "POST") {
      return jsonResponse(201, { client_id: "acme-corp", display_name: "Acme Corp", status: "inactive" });
    }
    throw new Error(`unexpected fetch ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderAt(path: string) {
  render(
    <I18nProvider>
    <MemoryRouter initialEntries={[path]}>
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
            <Route path="clients" element={<AdminClientsPage />} />
            <Route path="clients/:clientId" element={<AdminClientDetailPage />} />
          </Route>
        </Routes>
      </AdminAuthProvider>
    </MemoryRouter>
    </I18nProvider>,
  );
}

function sentBody(fetchMock: ReturnType<typeof stubFetch>, method: string, endsWith: string) {
  const call = fetchMock.mock.calls.find(
    ([input, init]) => (init as RequestInit | undefined)?.method === method && String(input).endsWith(endsWith),
  );
  return call ? (JSON.parse(String((call[1] as RequestInit).body)) as Record<string, unknown>) : undefined;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Billing: plans", () => {
  it("lists the real plans with their terms and how many clients are on them", async () => {
    stubFetch({ plans: [plan(), plan({ plan_id: "old", name: "Old", active: false, client_count: 0 })] });
    renderAt("/admin/billing");
    const row = (await screen.findByRole("rowheader", { name: "Pro" })).closest("tr") as HTMLElement;
    expect(within(row).getByText("49.90 USD")).toBeInTheDocument();
    expect(within(row).getByText("1000")).toBeInTheDocument();
    expect(within(row).getByText("200")).toBeInTheDocument();
    expect(within(row).getByText("Active")).toBeInTheDocument();
    const old = screen.getByRole("rowheader", { name: "Old" }).closest("tr") as HTMLElement;
    expect(within(old).getByText("Inactive")).toBeInTheDocument();
  });

  it("lets an owner create a plan with every term, through the API", async () => {
    const fetchMock = stubFetch({ plans: [] });
    renderAt("/admin/billing");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "+ New plan" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Create plan" })).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Plan name"), "Starter");
    await user.type(within(dialog).getByLabelText("Monthly price"), "19.5");
    await user.clear(within(dialog).getByLabelText("Currency"));
    await user.type(within(dialog).getByLabelText("Currency"), "eur");
    await user.type(within(dialog).getByLabelText("Included credits"), "500");
    await user.type(within(dialog).getByLabelText("Maximum daily quota"), "50");
    await user.click(within(dialog).getByRole("button", { name: "Create plan" }));
    expect(await screen.findByRole("rowheader", { name: "Starter" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(sentBody(fetchMock, "POST", "/plans")).toEqual({
      name: "Starter",
      monthly_price: "19.5",
      currency: "EUR",
      included_credits: 500,
      daily_quota: 50,
      active: true,
    });
  });

  it("edits a plan and deactivates it", async () => {
    const fetchMock = stubFetch();
    renderAt("/admin/billing");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Edit Pro" }));
    const dialog = await screen.findByRole("dialog");
    await user.clear(within(dialog).getByLabelText("Monthly price"));
    await user.type(within(dialog).getByLabelText("Monthly price"), "59");
    await user.click(within(dialog).getByRole("switch", { name: "Active" }));
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(sentBody(fetchMock, "PUT", "/plans/pro")).toMatchObject({ monthly_price: "59", active: false });
    const row = screen.getByRole("rowheader", { name: "Pro" }).closest("tr") as HTMLElement;
    expect(within(row).getByText("Inactive")).toBeInTheDocument();
  });

  it("shows the server's refusal and keeps the form open", async () => {
    stubFetch({ plans: [], refuse: { status: 409, detail: "a plan with this name already exists" } });
    renderAt("/admin/billing");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "+ New plan" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Plan name"), "Pro");
    await user.type(within(dialog).getByLabelText("Monthly price"), "1");
    await user.type(within(dialog).getByLabelText("Included credits"), "1");
    await user.type(within(dialog).getByLabelText("Maximum daily quota"), "1");
    await user.click(within(dialog).getByRole("button", { name: "Create plan" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("a plan with this name already exists");
  });

  it("is read-only for anyone but an owner", async () => {
    stubFetch({ role: "viewer" });
    renderAt("/admin/billing");
    expect(await screen.findByRole("rowheader", { name: "Pro" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "+ New plan" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Edit Pro" })).toBeNull();
  });

  it("says so when the plans cannot be loaded, instead of showing an empty list", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) return jsonResponse(200, { username: "root", role: "owner", client_ids: [] });
        return jsonResponse(503, { detail: "plans need the database (set SALES_AGENT_DATABASE_URL)" });
      }),
    );
    renderAt("/admin/billing");
    expect(await screen.findByRole("alert")).toHaveTextContent("plans need the database");
  });
});

describe("Client details: plan", () => {
  it("shows the plan the client is on, with the terms recorded for it", async () => {
    stubFetch({ current: assignment() });
    renderAt("/admin/clients/alpha");
    const heading = await screen.findByRole("heading", { name: "Plan" });
    const card = within(heading.closest(".admin-detail-card") as HTMLElement);
    expect(await card.findByText("49.90 USD")).toBeInTheDocument();
    expect(card.getByText("1000")).toBeInTheDocument();
    expect(card.getByLabelText("Plan")).toHaveValue("pro");
  });

  it("says a client is not on a plan, and lets an owner put it on one", async () => {
    const fetchMock = stubFetch();
    renderAt("/admin/clients/alpha");
    const heading = await screen.findByRole("heading", { name: "Plan" });
    const card = within(heading.closest(".admin-detail-card") as HTMLElement);
    expect(await card.findByText("This client is not on a plan.")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.selectOptions(card.getByLabelText("Plan"), "pro");
    await user.click(card.getByRole("button", { name: "Save plan" }));
    expect(await card.findByText("Saved.")).toBeInTheDocument();
    expect(sentBody(fetchMock, "PUT", "/clients/alpha/plan")).toEqual({ plan_id: "pro" });
  });

  it("can take a client off its plan", async () => {
    const fetchMock = stubFetch({ current: assignment() });
    renderAt("/admin/clients/alpha");
    const heading = await screen.findByRole("heading", { name: "Plan" });
    const card = within(heading.closest(".admin-detail-card") as HTMLElement);
    const user = userEvent.setup();
    await user.selectOptions(await card.findByLabelText("Plan"), "");
    await user.click(card.getByRole("button", { name: "Save plan" }));
    expect(await card.findByText("This client is not on a plan.")).toBeInTheDocument();
    expect(sentBody(fetchMock, "PUT", "/clients/alpha/plan")).toEqual({ plan_id: null });
  });

  it("offers only active plans, but keeps the current one visible when it was switched off", async () => {
    stubFetch({
      plans: [plan(), plan({ plan_id: "old", name: "Old", active: false }), plan({ plan_id: "gone", name: "Gone", active: false })],
      current: assignment({ plan_id: "old", plan_name: "Old" }),
    });
    renderAt("/admin/clients/alpha");
    const heading = await screen.findByRole("heading", { name: "Plan" });
    const select = await within(heading.closest(".admin-detail-card") as HTMLElement).findByLabelText("Plan");
    const names = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(names).toEqual(["Aucun plan", "Pro", "Old (inactive)"]);
  });

  it("only lets an owner change it", async () => {
    stubFetch({ role: "editor", current: assignment() });
    renderAt("/admin/clients/alpha");
    const heading = await screen.findByRole("heading", { name: "Plan" });
    const card = within(heading.closest(".admin-detail-card") as HTMLElement);
    expect(await card.findByLabelText("Plan")).toBeDisabled();
  });

  it("shows the server's refusal", async () => {
    stubFetch({ refuse: { status: 409, detail: "this plan is deactivated: choose an active plan" } });
    renderAt("/admin/clients/alpha");
    const heading = await screen.findByRole("heading", { name: "Plan" });
    const card = within(heading.closest(".admin-detail-card") as HTMLElement);
    const user = userEvent.setup();
    await user.selectOptions(await card.findByLabelText("Plan"), "pro");
    await user.click(card.getByRole("button", { name: "Save plan" }));
    expect(await card.findByRole("alert")).toHaveTextContent("this plan is deactivated");
  });
});

describe("New client: plan", () => {
  async function fill(dialog: HTMLElement) {
    const user = userEvent.setup();
    await user.type(within(dialog).getByLabelText("Nom du client"), "Acme Corp");
    await user.type(within(dialog).getByLabelText("Login"), "acme.admin");
    await user.type(within(dialog).getByLabelText("Mot de passe temporaire"), "temp-pass-123");
    await user.type(within(dialog).getByLabelText("WhatsApp number"), "+216 22 768 307");
    return user;
  }

  it("sends the chosen active plan with the new client", async () => {
    const fetchMock = stubFetch({ plans: [plan(), plan({ plan_id: "old", name: "Old", active: false })] });
    renderAt("/admin/clients");
    const user0 = userEvent.setup();
    await user0.click(await screen.findByRole("button", { name: "+ Ajouter un client" }));
    const dialog = await screen.findByRole("dialog");
    const select = await within(dialog).findByLabelText("Plan");
    expect(within(select).queryByText(/Old/)).toBeNull();
    const user = await fill(dialog);
    await user.selectOptions(select, "pro");
    await user.click(within(dialog).getByRole("button", { name: "Ajouter un client" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(sentBody(fetchMock, "POST", "/admin/v1/clients")).toMatchObject({ plan_id: "pro" });
  });

  it("sends no plan when none is chosen", async () => {
    const fetchMock = stubFetch();
    renderAt("/admin/clients");
    const user0 = userEvent.setup();
    await user0.click(await screen.findByRole("button", { name: "+ Ajouter un client" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByLabelText("Plan");
    const user = await fill(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Ajouter un client" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(sentBody(fetchMock, "POST", "/admin/v1/clients")).not.toHaveProperty("plan_id");
  });
});
