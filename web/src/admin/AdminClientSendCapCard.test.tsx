import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AdminAuthProvider } from "./AdminAuthContext";
import { AdminClientSendCapCard } from "./pages/AdminClientSendCapCard";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function cap(over: Record<string, unknown> = {}) {
  return {
    client_id: "alpha",
    channel: "email",
    daily_cap: null,
    plan_daily_quota: null,
    effective_cap: null,
    used_today: 0,
    remaining_today: null,
    day_utc: "2026-01-15",
    status: "not_configured",
    max_cap: 100000,
    ...over,
  };
}

function stub(opts: { role?: string; current?: unknown; routes?: Record<string, (init?: RequestInit) => Response> } = {}) {
  const { role = "owner", routes = {} } = opts;
  const current = opts.current ?? cap();
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const key = `${init?.method ?? "GET"} ${url}`;
      calls.push(key);
      if (routes[key]) return routes[key](init);
      if (url.endsWith("/auth/me")) return json(200, { username: "alice", role, client_ids: [] });
      if (key === "GET /admin/v1/clients/alpha/send-cap") return json(200, current);
      throw new Error(`unexpected fetch ${key}`);
    }),
  );
  return calls;
}

function renderCard() {
  render(
    <MemoryRouter>
      <AdminAuthProvider>
        <AdminClientSendCapCard clientId="alpha" />
      </AdminAuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("AdminClientSendCapCard", () => {
  it("says that a client without a limit cannot send", async () => {
    stub();
    renderCard();
    expect(await screen.findByTestId("send-cap-status")).toHaveTextContent("cannot send any email");
    expect(screen.queryByRole("button", { name: "Remove limit" })).not.toBeInTheDocument();
  });

  it("shows the limit, the plan quota that holds it, and today's use", async () => {
    stub({
      current: cap({ daily_cap: 50, plan_daily_quota: 3, effective_cap: 3, used_today: 3, remaining_today: 0, status: "limit_reached" }),
    });
    renderCard();
    expect(await screen.findByTestId("send-cap-status")).toHaveTextContent("limit is reached");
    expect(screen.getByText("Effective limit").nextSibling).toHaveTextContent("3");
    expect(screen.getByText("Limit set by the admin").nextSibling).toHaveTextContent("50");
    expect(screen.getByText(/the quota applies/)).toBeInTheDocument();
    expect(screen.getByLabelText("Daily limit (emails per day)")).toHaveValue("50");
  });

  it("saves a whole number, refuses anything else before calling the server", async () => {
    const calls = stub({
      routes: {
        "PUT /admin/v1/clients/alpha/send-cap": (init) => {
          expect(JSON.parse(String(init?.body))).toEqual({ daily_cap: 40 });
          return json(200, cap({ daily_cap: 40, effective_cap: 40, status: "ok" }));
        },
      },
    });
    renderCard();
    const user = userEvent.setup();
    const input = await screen.findByLabelText("Daily limit (emails per day)");
    for (const bad of ["0", "2.5", "100001"]) {
      await user.clear(input);
      await user.type(input, bad);
      expect(screen.getByRole("button", { name: "Save limit" })).toBeDisabled();
    }
    await user.clear(input);
    await user.type(input, "40");
    await user.click(screen.getByRole("button", { name: "Save limit" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(screen.getByTestId("send-cap-status")).toHaveTextContent("can go out");
    expect(calls.filter((c) => c.startsWith("PUT"))).toHaveLength(1);
  });

  it("removes the limit", async () => {
    stub({
      current: cap({ daily_cap: 5, effective_cap: 5, status: "ok" }),
      routes: { "DELETE /admin/v1/clients/alpha/send-cap": () => json(200, cap()) },
    });
    renderCard();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Remove limit" }));
    expect(await screen.findByTestId("send-cap-status")).toHaveTextContent("cannot send any email");
    expect(screen.getByLabelText("Daily limit (emails per day)")).toHaveValue("");
  });

  it("shows the server's refusal", async () => {
    stub({ routes: { "PUT /admin/v1/clients/alpha/send-cap": () => json(503, { detail: "the send cap is unavailable" }) } });
    renderCard();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Daily limit (emails per day)"), "7");
    await user.click(screen.getByRole("button", { name: "Save limit" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("is read-only for an admin who is not an owner", async () => {
    stub({ role: "editor", current: cap({ daily_cap: 5, effective_cap: 5, status: "ok" }) });
    renderCard();
    expect(await screen.findByLabelText("Daily limit (emails per day)")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove limit" })).toBeDisabled();
  });
});
