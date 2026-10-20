import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nProvider } from "../i18n";
import { FollowUpSettingsCard } from "./FollowUpSettingsCard";

const logout = vi.fn(async () => undefined);
vi.mock("./ClientAuthContext", () => ({ useClientAuth: () => ({ logout }) }));

const URL_ = "/client/v1/followup-settings";

function settings(over: Record<string, unknown> = {}) {
  return {
    version: 3,
    configured: true,
    max_followups: 3,
    gap_days: 2,
    window: null,
    timezone: "Africa/Tunis",
    custom_schedule: false,
    defaults: { max_followups: 3, gap_days: 2 },
    bounds: { max_followups: [0, 6], gap_days: [1, 30] },
    ...over,
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function stub(handlers: Record<string, Array<() => Response>>) {
  const calls: Array<{ key: string; body: unknown }> = [];
  const used: Record<string, number> = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input)}`;
      calls.push({ key, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const list = handlers[key];
      if (!list) throw new Error(`unexpected fetch ${key}`);
      const index = Math.min(used[key] ?? 0, list.length - 1);
      used[key] = (used[key] ?? 0) + 1;
      return list[index]!();
    }),
  );
  return calls;
}

function renderCard() {
  render(
    <I18nProvider>
      <FollowUpSettingsCard />
    </I18nProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  logout.mockClear();
});

describe("follow-up settings", () => {
  it("shows the plan in force and the default", async () => {
    stub({ [`GET ${URL_}`]: [() => json(200, settings({ max_followups: 4, gap_days: 5 }))] });
    renderCard();
    expect(await screen.findByLabelText("Number of follow-ups")).toHaveValue(4);
    expect(screen.getByLabelText("Days between two follow-ups")).toHaveValue(5);
    expect(screen.getByTestId("followup-default")).toHaveTextContent("3 follow-ups, 2 days apart");
    expect(screen.queryByLabelText("From")).not.toBeInTheDocument(); // no hours set
  });

  it("shows the loading state, then the error with a retry", async () => {
    stub({ [`GET ${URL_}`]: [() => json(503, { detail: "settings are temporarily unavailable" }), () => json(200, settings())] });
    const user = userEvent.setup();
    renderCard();
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Number of follow-ups")).toBeInTheDocument();
  });

  it("saves the number, the gap and the hours, with the version it was read on", async () => {
    const saved = settings({ version: 4, max_followups: 5, gap_days: 3, window: { start: "09:00", end: "17:30", timezone: "Africa/Tunis" } });
    const calls = stub({ [`GET ${URL_}`]: [() => json(200, settings())], [`PUT ${URL_}`]: [() => json(200, saved)] });
    const user = userEvent.setup();
    renderCard();
    const max = await screen.findByLabelText("Number of follow-ups");
    await user.clear(max);
    await user.type(max, "5");
    const gap = screen.getByLabelText("Days between two follow-ups");
    await user.clear(gap);
    await user.type(gap, "3");
    await user.click(screen.getByLabelText("Only follow up during set hours"));
    const end = screen.getByLabelText("Until");
    await user.clear(end);
    await user.type(end, "17:30");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByTestId("followup-saved")).toBeInTheDocument();
    const put = calls.find((c) => c.key === `PUT ${URL_}`)!;
    expect(put.body).toEqual({
      expected_version: 3,
      max_followups: 5,
      gap_days: 3,
      window: { start: "09:00", end: "17:30", timezone: "Africa/Tunis" },
    });
  });

  it.each([
    ["Number of follow-ups", "7", "whole number from 0 to 6"],
    ["Number of follow-ups", "-1", "whole number from 0 to 6"],
    ["Days between two follow-ups", "0", "from 1 to 30"],
    ["Days between two follow-ups", "31", "from 1 to 30"],
  ])("refuses %s = %s before asking the server", async (label, value, message) => {
    const calls = stub({ [`GET ${URL_}`]: [() => json(200, settings())] });
    const user = userEvent.setup();
    renderCard();
    const field = await screen.findByLabelText(label);
    await user.clear(field);
    await user.type(field, value);
    expect(await screen.findByText(new RegExp(message))).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(calls.filter((c) => c.key.startsWith("PUT"))).toEqual([]);
  });

  it("refuses closing hours that are not after the opening hour", async () => {
    stub({ [`GET ${URL_}`]: [() => json(200, settings({ window: { start: "09:00", end: "18:00", timezone: "Africa/Tunis" } }))] });
    const user = userEvent.setup();
    renderCard();
    const end = await screen.findByLabelText("Until");
    await user.clear(end);
    await user.type(end, "08:00");
    expect(await screen.findByText("The opening hour must be earlier than the closing hour.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("restores the defaults in the form without saving them", async () => {
    const calls = stub({ [`GET ${URL_}`]: [() => json(200, settings({ max_followups: 5, gap_days: 9, window: { start: "09:00", end: "18:00", timezone: "UTC" } }))] });
    const user = userEvent.setup();
    renderCard();
    await user.click(await screen.findByRole("button", { name: "Use the defaults" }));
    expect(screen.getByLabelText("Number of follow-ups")).toHaveValue(3);
    expect(screen.getByLabelText("Days between two follow-ups")).toHaveValue(2);
    expect(screen.queryByLabelText("Until")).not.toBeInTheDocument();
    expect(calls.filter((c) => c.key.startsWith("PUT"))).toEqual([]);
  });

  it("shows the Brain's own words when it refuses the settings, and keeps what was typed", async () => {
    stub({
      [`GET ${URL_}`]: [() => json(200, settings())],
      [`PUT ${URL_}`]: [() => json(422, { detail: ["follow_up.situations.no_response.steps[0].after: shorter than follow_up.limits.min_gap (P3D)"] })],
    });
    const user = userEvent.setup();
    renderCard();
    const gap = await screen.findByLabelText("Days between two follow-ups");
    await user.clear(gap);
    await user.type(gap, "1");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByTestId("followup-error")).toHaveTextContent("shorter than follow_up.limits.min_gap");
    expect(gap).toHaveValue(1);
  });

  it("reloads what is stored after a conflict", async () => {
    stub({
      [`GET ${URL_}`]: [() => json(200, settings()), () => json(200, settings({ version: 4, gap_days: 7 }))],
      [`PUT ${URL_}`]: [() => json(409, { detail: "the settings changed since they were loaded; reload and retry" })],
    });
    const user = userEvent.setup();
    renderCard();
    const gap = await screen.findByLabelText("Days between two follow-ups");
    await user.clear(gap);
    await user.type(gap, "4");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByLabelText("Days between two follow-ups")).toHaveValue(7));
  });

  it("says the business settings must be completed first, and does not allow saving", async () => {
    stub({ [`GET ${URL_}`]: [() => json(200, settings({ configured: false, version: null }))] });
    renderCard();
    expect(await screen.findByText("Complete your business settings before changing the follow-ups.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("warns that uneven waits will be replaced by an even one", async () => {
    stub({ [`GET ${URL_}`]: [() => json(200, settings({ custom_schedule: true }))] });
    renderCard();
    expect(await screen.findByTestId("followup-custom")).toHaveTextContent("uneven waits");
  });

  it("signs the client out when the session ended", async () => {
    stub({ [`GET ${URL_}`]: [() => json(401, { detail: "not authenticated" })] });
    renderCard();
    await waitFor(() => expect(logout).toHaveBeenCalled());
  });
});
