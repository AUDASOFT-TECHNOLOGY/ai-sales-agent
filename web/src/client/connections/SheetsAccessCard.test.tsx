import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nProvider } from "../../i18n";
import { SheetsAccessCard, sheetsReturnOf } from "./SheetsAccessCard";

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

function access(over: Record<string, unknown> = {}) {
  return { state: "not_connected", address: null, spreadsheet_saved: true, message: "", ...over };
}

function stub(routes: Record<string, () => Response>) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input).replace("/client/v1", "")}`;
      calls.push(key);
      const handler = routes[key];
      if (!handler) throw new Error(`unexpected fetch ${key}`);
      return handler();
    }),
  );
  return calls;
}

function renderCard(go?: (url: string) => void) {
  return render(
    <I18nProvider>
      <SheetsAccessCard linkKey="1" go={go} />
    </I18nProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("SheetsAccessCard", () => {
  it("sends the browser to Google when the client clicks Authorise, and asks for nothing else", async () => {
    const go = vi.fn();
    const calls = stub({
      "GET /sheets-access": () => json(access()),
      "POST /sheets-access/start": () => json({ authorize_url: "https://accounts.example/auth?state=s" }),
    });
    renderCard(go);
    await userEvent.click(await screen.findByRole("button", { name: /authorise revansa/i }));
    await waitFor(() => expect(go).toHaveBeenCalledWith("https://accounts.example/auth?state=s"));
    expect(calls).toEqual(["GET /sheets-access", "POST /sheets-access/start"]);
  });

  it("asks for the link first", async () => {
    stub({ "GET /sheets-access": () => json(access({ spreadsheet_saved: false })) });
    renderCard();
    const button = await screen.findByRole("button", { name: /authorise revansa/i });
    await waitFor(() => expect(button).toBeDisabled());
    expect(button).toHaveAttribute("title", expect.stringMatching(/save the link/i));
  });

  it("shows the client's own Google address once connected, and can disconnect", async () => {
    stub({
      "GET /sheets-access": () => json(access({ state: "connected", address: "owner@example.com" })),
      "POST /sheets-access/disconnect": () => json(access()),
    });
    renderCard();
    expect(await screen.findByText(/owner@example.com/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /disconnect google/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /authorise revansa/i })).toBeInTheDocument());
  });

  it("explains that Revansa is not set up yet instead of offering a dead button", async () => {
    stub({ "GET /sheets-access": () => json(access({ state: "unavailable" })) });
    renderCard();
    expect(await screen.findByText(/finish setting it up/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /authorise revansa/i })).toBeNull();
  });

  it("reads only the known return outcomes", () => {
    expect(sheetsReturnOf("?google_sheets=connected")).toBe("connected");
    expect(sheetsReturnOf("?google_sheets=<script>")).toBeNull();
    expect(sheetsReturnOf("")).toBeNull();
  });
});
