import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../../i18n";
import { UsagePage } from "./UsagePage";

const usage = {
  plan_name: "Plan Pro",
  day_utc: "2026-10-10",
  month: "2026-10",
  daily_limit: 20,
  daily_used: 17,
  daily_remaining: 3,
  daily_percent: 85,
  month_limit: 620,
  month_actions_used: 37,
  month_percent: 5,
  included_credits: 5000,
  credits_used: 2000,
  credits_today: 300,
  credits_remaining: 3000,
  credits_percent: 40,
  credits_basis: "measured",
  calls: 40,
  calls_without_figures: 2,
  state: "warning",
  message: "You have used more than 80% of today's sending limit.",
  action: "Prioritise your best prospects.",
  days: [
    { day: "2026-10-09", actions: 20, credits: 900 },
    { day: "2026-10-10", actions: 17, credits: 300 },
  ],
};

function show(body: unknown, status = 200) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status })));
  return render(
    <MemoryRouter>
      <I18nProvider>
        <UsagePage />
      </I18nProvider>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("UsagePage", () => {
  it("leads with the credits left, the pace of the month, then both quotas", async () => {
    show(usage);
    const credits = await screen.findByTestId("usage-credits");
    expect(within(credits).getByText("3,000")).toBeTruthy();
    expect(credits.textContent).toContain("2,000");
    expect(screen.getByTestId("usage-pace").getAttribute("data-run-out")).toBe("true"); // 200 a day, 31 days
    expect(within(screen.getByTestId("usage-daily")).getByText("17")).toBeTruthy();
    expect(screen.getByTestId("usage-daily").getAttribute("data-level")).toBe("warning");
    expect(within(screen.getByTestId("usage-month")).getByText(/620/)).toBeTruthy();
  });

  it("offers a way out of an alert", async () => {
    show(usage);
    const notice = await screen.findByTestId("usage-notice");
    expect(notice.textContent).toContain("80%");
    expect(within(notice).getByRole("link").getAttribute("href")).toBe("/account");
  });

  it("charts each day and draws the daily quota only on the actions", async () => {
    show(usage);
    await screen.findByTestId("usage-chart");
    expect(screen.queryByTestId("usage-line")).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: /actions/i }));
    expect(screen.getByTestId("usage-line")).toBeTruthy();
    expect(within(screen.getByTestId("usage-days")).getAllByRole("row")).toHaveLength(3);
  });

  it("says so when the credits cannot be read, instead of showing a zero", async () => {
    show({ ...usage, credits_used: null, credits_remaining: null, credits_percent: null, credits_basis: "unavailable", state: "ok", message: "", action: "" });
    const credits = await screen.findByTestId("usage-credits");
    expect(within(credits).getByText(/not available/i)).toBeTruthy();
    expect(screen.queryByTestId("usage-pace")).toBeNull();
  });

  it("tells when the usage cannot be read", async () => {
    show({}, 503);
    expect(await screen.findByTestId("usage-failed")).toBeTruthy();
  });
});
