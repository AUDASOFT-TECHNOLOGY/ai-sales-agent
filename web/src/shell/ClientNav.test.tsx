import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { AppShell } from "./AppShell";
import { ClientAuthProvider } from "../client/ClientAuthContext";
import { ClientRoute } from "../client/ClientRoute";
import { HomeLinks } from "../client/HomeLinks";
import { ClientNotificationsPage } from "../client/notifications/ClientNotificationsPage";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const PROFILE = { name: "Acme", login: "a", whatsapp_number: "+1", status: "active", must_change_password: false, whatsapp_reconnect_required: false };

function stub(over: { unread?: number; subscription?: unknown; failNotifications?: boolean } = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return json(200, PROFILE);
      if (url.endsWith("/notifications")) {
        if (over.failNotifications) return json(500, {});
        return json(200, { items: [], total: 0, unread_count: over.unread ?? 0 });
      }
      if (url.endsWith("/subscription")) return json(200, over.subscription ?? { status: "active", plan: null, next_billing_at: null, unpaid_invoices: 0, overdue_invoices: 0, last_payment: null });
      return json(200, { proposals: [] });
    }),
  );
}

function renderShell(path = "/home") {
  render(
    <I18nProvider>
      <MemoryRouter initialEntries={[path]}>
        <ClientAuthProvider>
          <Routes>
            <Route element={<ClientRoute><Outlet /></ClientRoute>}>
              <Route element={<AppShell />}>
                <Route path="home" element={<HomeLinks />} />
                <Route path="notifications" element={<ClientNotificationsPage />} />
              </Route>
            </Route>
          </Routes>
        </ClientAuthProvider>
      </MemoryRouter>
    </I18nProvider>,
  );
}

beforeEach(() => vi.stubGlobal("scrollTo", vi.fn()));
afterEach(() => vi.unstubAllGlobals());

describe("Client menu", () => {
  it("offers dashboard, billing and notifications with the unread count", async () => {
    stub({ unread: 3 });
    renderShell();
    const side = (await screen.findAllByRole("navigation", { name: "Main menu" }))[0]!;
    expect(await within(side).findByRole("link", { name: /Dashboard/ })).toHaveAttribute("href", "/dashboard");
    expect(within(side).getByRole("link", { name: /Billing/ })).toHaveAttribute("href", "/billing");
    const bell = await within(side).findByRole("link", { name: /Notifications/ });
    expect(bell).toHaveAttribute("href", "/notifications");
    expect(await within(bell).findByText("3")).toBeInTheDocument();
  });

  it("shows no badge when nothing is unread", async () => {
    stub({ unread: 0 });
    renderShell();
    const bell = await screen.findAllByRole("link", { name: "Notifications" });
    expect(bell.length).toBeGreaterThan(0);
    expect(screen.queryByText("0")).toBeNull();
  });

  it("mounts the notifications page", async () => {
    stub({ unread: 0 });
    renderShell("/notifications");
    expect(await screen.findByText("Nothing needs your attention.")).toBeInTheDocument();
  });
});

describe("Home shortcuts", () => {
  it("flags overdue invoices and open notifications", async () => {
    stub({ unread: 2, subscription: { status: "active", plan: null, next_billing_at: null, unpaid_invoices: 1, overdue_invoices: 1, last_payment: null } });
    render(
      <I18nProvider>
        <MemoryRouter>
          <HomeLinks />
        </MemoryRouter>
      </I18nProvider>,
    );
    expect(await screen.findByText(/1.* overdue invoice/)).toBeInTheDocument();
    expect(await screen.findByText(/2.* unread notification/)).toBeInTheDocument();
  });

  it("says so when notifications cannot be read", async () => {
    stub({ failNotifications: true });
    render(
      <I18nProvider>
        <MemoryRouter>
          <HomeLinks />
        </MemoryRouter>
      </I18nProvider>,
    );
    expect(await screen.findByText("Notifications could not be read.")).toBeInTheDocument();
  });
});
