import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AdminAuthProvider } from "./AdminAuthContext";
import { AdminClientActivationCard, AdminEmailActivationRequests } from "./pages/AdminEmailActivation";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const row = (over: Record<string, unknown> = {}) => ({
  client_id: "alpha",
  client_name: "Alpha Co",
  sender_name: "Alpha Sales",
  status: "pending",
  requested_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-01T10:00:00Z",
  updated_by: "client:alpha",
  reason: null,
  email_allowed: true,
  mailbox: "mailbox_not_assigned",
  ...over,
});

function stub(routes: Record<string, (body: unknown) => Response>, role = "owner") {
  const calls: Array<{ key: string; body: unknown }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const key = `${init?.method ?? "GET"} ${url.replace("/admin/v1", "")}`;
      if (url.endsWith("/auth/me")) return json(200, { username: "alice", role, client_ids: [] });
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ key, body });
      const handler = routes[key];
      if (!handler) throw new Error(`unexpected fetch ${key}`);
      return handler(body);
    }),
  );
  return calls;
}

function renderCard() {
  render(
    <MemoryRouter>
      <AdminAuthProvider>
        <AdminClientActivationCard clientId="alpha" />
      </AdminAuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("AdminEmailActivationRequests", () => {
  it("lists the pending requests with client, sender name, date and status", async () => {
    stub({
      "GET /email-activations?status=open": () =>
        json(200, [row(), row({ client_id: "beta", client_name: "Beta", sender_name: null, status: "in_progress" })]),
    });
    render(
      <MemoryRouter>
        <AdminEmailActivationRequests />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("link", { name: "Alpha Co" })).toHaveAttribute("href", "/admin/clients/alpha");
    expect(screen.getByText("Alpha Sales")).toBeInTheDocument();
    expect(screen.getByText("Pending")).toBeInTheDocument();
    expect(screen.getByText("In progress")).toBeInTheDocument();
    expect(screen.getByText("Not confirmed yet")).toBeInTheDocument();
  });

  it("says when nothing waits", async () => {
    stub({ "GET /email-activations?status=open": () => json(200, []) });
    render(
      <MemoryRouter>
        <AdminEmailActivationRequests />
      </MemoryRouter>,
    );
    expect(await screen.findByText("No request is waiting.")).toBeInTheDocument();
  });
});

describe("AdminClientActivationCard", () => {
  it("moves a pending request in progress", async () => {
    const calls = stub({
      "GET /clients/alpha/email-activation": () => json(200, row()),
      "POST /clients/alpha/email-activation/start": () => json(200, row({ status: "in_progress" })),
    });
    renderCard();
    await userEvent.click(await screen.findByRole("button", { name: "Mark in progress" }));
    expect(await screen.findByRole("form", { name: "Activate the mailbox" })).toBeInTheDocument();
    expect(calls.map((c) => c.key)).toContain("POST /clients/alpha/email-activation/start");
  });

  it("requires a reason to reject, and sends it", async () => {
    const calls = stub({
      "GET /clients/alpha/email-activation": () => json(200, row()),
      "POST /clients/alpha/email-activation/reject": () =>
        json(200, row({ status: "rejected", reason: "Not eligible" })),
    });
    renderCard();
    const button = await screen.findByRole("button", { name: "Reject" });
    expect(button).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Reject this request — reason/), "Not eligible");
    await userEvent.click(button);
    expect(await screen.findByText("Rejected")).toBeInTheDocument();
    expect(calls.find((c) => c.key.endsWith("/reject"))?.body).toEqual({ reason: "Not eligible" });
  });

  it("activates with the address and password, then shows Active without the password", async () => {
    const calls = stub({
      "GET /clients/alpha/email-activation": () => json(200, row({ status: "in_progress" })),
      "POST /clients/alpha/email-activation/activate": () => json(200, row({ status: "active", mailbox: "ready" })),
    });
    renderCard();
    expect(await screen.findByText(/for the sender name “Alpha Sales”/)).toBeInTheDocument();
    const activate = screen.getByRole("button", { name: "Activate" });
    expect(activate).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Mailbox address"), "alpha@mail.example.com");
    await userEvent.type(screen.getByLabelText("Mailbox password"), "S3cret-pass");
    await userEvent.click(activate);
    expect(await screen.findByText("Active")).toBeInTheDocument();
    expect(calls.find((c) => c.key.endsWith("/activate"))?.body).toEqual({
      address: "alpha@mail.example.com",
      password: "S3cret-pass",
    });
    expect(document.body.textContent).not.toContain("S3cret-pass");
  });

  it("keeps the request in progress and shows the login error when activation fails", async () => {
    stub({
      "GET /clients/alpha/email-activation": () => json(200, row({ status: "in_progress" })),
      "POST /clients/alpha/email-activation/activate": () =>
        json(422, {
          detail: {
            message: "the mailbox could not be authenticated: nothing was saved",
            problems: ["SMTP: the server refused the login: check the username and the password."],
          },
        }),
    });
    renderCard();
    await userEvent.type(await screen.findByLabelText("Mailbox address"), "alpha@mail.example.com");
    await userEvent.type(screen.getByLabelText("Mailbox password"), "wrong-pass");
    await userEvent.click(screen.getByRole("button", { name: "Activate" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/SMTP: the server refused the login/);
    expect(screen.getByText("In progress")).toBeInTheDocument();
    expect(screen.getByLabelText("Mailbox password")).toHaveValue("");
  });

  it("is read-only for a non-owner", async () => {
    stub({ "GET /clients/alpha/email-activation": () => json(200, row()) }, "viewer");
    renderCard();
    expect(await screen.findByRole("button", { name: "Mark in progress" })).toBeDisabled();
  });

  it("says when the client has not asked", async () => {
    stub({ "GET /clients/alpha/email-activation": () => json(200, null) });
    renderCard();
    expect(await screen.findByText(/has not asked for its email to be activated/)).toBeInTheDocument();
  });
});
