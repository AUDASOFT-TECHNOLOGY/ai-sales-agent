import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AdminAuthProvider } from "./AdminAuthContext";
import { AdminClientSourcesCard, mappingToText, parseMapping } from "./pages/AdminClientSourcesCard";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const SOURCE = "/admin/v1/clients/alpha/source";

function stub(source: unknown, role = "owner") {
  const calls: Array<{ key: string; body?: unknown }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const key = `${init?.method ?? "GET"} ${url}`;
      calls.push({ key, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
      if (url.endsWith("/auth/me")) return json(200, { username: "alice", role, client_ids: [] });
      if (key === `GET ${SOURCE}`) return json(200, source);
      if (key === `PUT ${SOURCE}`) return json(200, { provider: "hubspot", configured: true, settings: JSON.parse(String(init?.body)) });
      if (key === "POST /admin/v1/clients/alpha/lanes/adopt") return json(201, { lane: "main", source: "hubspot", channel: "email", version: 2 });
      throw new Error(`unexpected fetch ${key}`);
    }),
  );
  return calls;
}

function renderCard() {
  render(
    <MemoryRouter>
      <AdminAuthProvider>
        <AdminClientSourcesCard clientId="alpha" />
      </AdminAuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("status mapping text", () => {
  it("parses one line per HubSpot value and reports a line it cannot read", () => {
    expect(parseMapping("NEW = new\nBAD_TIMING = paused\n\nUNQUALIFIED=closed")).toEqual({
      mapping: { NEW: "new", BAD_TIMING: "paused", UNQUALIFIED: "closed" },
      bad: false,
    });
    expect(parseMapping("NEW = active").bad).toBe(true);
    expect(parseMapping("NEW").bad).toBe(true);
    expect(mappingToText({ NEW: "new" })).toBe("NEW = new");
  });
});

describe("AdminClientSourcesCard", () => {
  it("saves the statuses with the other settings kept", async () => {
    const calls = stub({ provider: "hubspot", configured: false, settings: { do_not_contact_property: "hs_email_optout" } });
    renderCard();
    await userEvent.type(await screen.findByLabelText("Meaning of each value"), "NEW = new{Enter}BAD_TIMING = paused");
    await userEvent.click(screen.getByRole("button", { name: /save statuses/i }));
    await waitFor(() => expect(calls.some((c) => c.key === `PUT ${SOURCE}`)).toBe(true));
    expect(calls.find((c) => c.key === `PUT ${SOURCE}`)?.body).toEqual({
      do_not_contact_property: "hs_email_optout",
      status_property: "hs_lead_status",
      status_mapping: { NEW: "new", BAD_TIMING: "paused" },
    });
    expect(await screen.findByText("HubSpot statuses saved.")).toBeInTheDocument();
  });

  it("refuses a line it cannot read before sending anything", async () => {
    const calls = stub({ provider: "hubspot", configured: false, settings: null });
    renderCard();
    await userEvent.type(await screen.findByLabelText("Meaning of each value"), "NEW = active");
    await userEvent.click(screen.getByRole("button", { name: /save statuses/i }));
    expect(await screen.findByText(/must look like/i)).toBeInTheDocument();
    expect(calls.some((c) => c.key.startsWith("PUT"))).toBe(false);
  });

  it("moves the client onto lanes with one click", async () => {
    const calls = stub({ provider: "", configured: false, settings: null });
    renderCard();
    await userEvent.click(await screen.findByRole("button", { name: /move onto lanes/i }));
    expect(await screen.findByText(/lane “main”/)).toBeInTheDocument();
    expect(calls.find((c) => c.key.endsWith("/lanes/adopt"))?.body).toEqual({ confirm_new_ids: false });
  });

  it("is read-only for an administrator who is not an owner", async () => {
    stub({ provider: "hubspot", configured: true, settings: { status_mapping: { NEW: "new" } } }, "operator");
    renderCard();
    const save = await screen.findByRole("button", { name: /save statuses/i });
    await waitFor(() => expect(save).toBeDisabled());
  });
});
