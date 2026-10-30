import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { ClientAuthProvider } from "../client/ClientAuthContext";
import { UpgradeBanner } from "../client/UpgradeBanner";
import { PlansPage } from "../client/PlansPage";
import { LandingPage } from "./LandingPage";
import { SignupPage } from "./SignupPage";

const PLANS = [
  { plan_id: "starter", name: "Starter", monthly_price: "49.00", currency: "USD", billing_period: "monthly", included_credits: 100000, daily_quota: 5000 },
  { plan_id: "business", name: "Business", monthly_price: "129.00", currency: "USD", billing_period: "monthly", included_credits: 350000, daily_quota: 15000 },
  { plan_id: "enterprise", name: "Enterprise", monthly_price: "299.00", currency: "USD", billing_period: "monthly", included_credits: 1000000, daily_quota: 40000 },
];
const PROFILE = { name: "Acme", login: "o@acme.test", whatsapp_number: null, status: "active", must_change_password: false, whatsapp_reconnect_required: false };

type Handler = (init?: RequestInit) => Response;

/** Answers `/client/v1/*` from a table; any path not listed is a 401 (anonymous visitor). */
function serve(routes: Record<string, Handler>) {
  const calls: Array<{ path: string; init?: RequestInit }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const path = String(input).replace("/client/v1", "");
      calls.push({ path, init });
      return routes[path]?.(init) ?? new Response("{}", { status: 401 });
    }),
  );
  return calls;
}
const json = (body: unknown, status = 200): Handler => () => new Response(JSON.stringify(body), { status });

function at(path: string, ui: React.ReactNode) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <I18nProvider>
        <ClientAuthProvider>{ui}</ClientAuthProvider>
      </I18nProvider>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("Landing page", () => {
  it("shows the plans exactly as the server sends them, highlights the middle one and links each to sign-up", async () => {
    serve({ "/public/plans": json({ plans: PLANS }) });
    at("/", <LandingPage />);
    const business = await screen.findByTestId("landing-plan-business");
    expect(business.className).toContain("is-featured");
    expect(screen.getByTestId("landing-plan-starter").className).not.toContain("is-featured");
    expect(business.textContent).toContain("$129");
    expect(business.textContent).toContain("350,000 credits included");
    expect(business.textContent).toContain("15,000 actions per day");
    const link = business.querySelector("a")!;
    expect(link.getAttribute("href")).toBe("/signup?plan=business");
  });

  it("changes a price when the plan changes: nothing is typed in the page", async () => {
    serve({ "/public/plans": json({ plans: [{ ...PLANS[0]!, monthly_price: "59.00", included_credits: 123 }] }) });
    at("/", <LandingPage />);
    const starter = await screen.findByTestId("landing-plan-starter");
    expect(starter.textContent).toContain("$59");
    expect(starter.textContent).toContain("123 credits included");
    expect(starter.className).not.toContain("is-featured"); // one plan: none is singled out
  });

  it("says plans are unavailable instead of inventing prices", async () => {
    serve({ "/public/plans": json({ detail: "plans are unavailable" }, 503) });
    at("/", <LandingPage />);
    expect(await screen.findByText(/Plans are not available right now/)).toBeInTheDocument();
    expect(screen.queryByText(/\$49/)).toBeNull();
  });

  it("states the hero, the Meta billing caveat and the demo data are fictional", async () => {
    serve({ "/public/plans": json({ plans: PLANS }) });
    at("/", <LandingPage />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Your AI Sales Agent. Always Selling.");
    expect(screen.getAllByText("Start for Free").length).toBeGreaterThan(0);
    expect(await screen.findByText(/billed separately/)).toBeInTheDocument();
    expect(screen.getByText(/Demo data\. No real conversation/)).toBeInTheDocument();
    expect(screen.getAllByText(/Coming soon/).length).toBeGreaterThan(0); // legal pages: no invented links
  });
});

describe("Free sign-up", () => {
  it("creates the account, then lands on the plans page with the chosen plan", async () => {
    const calls = serve({
      "/public/plans": json({ plans: PLANS }),
      "/auth/signup": json({ status: "created" }, 201),
    });
    // the session only exists once the sign-up succeeded
    let signedUp = false;
    const base = globalThis.fetch as unknown as (i: string, o?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
      if (String(input).endsWith("/auth/signup")) signedUp = true;
      if (String(input).endsWith("/auth/me")) return signedUp ? new Response(JSON.stringify(PROFILE)) : new Response("{}", { status: 401 });
      return base(input, init);
    }));
    at(
      "/signup?plan=business",
      <Routes>
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/plans" element={<p>plans page</p>} />
      </Routes>,
    );
    expect(await screen.findByText(/You chose the Business plan/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Business name/), { target: { value: "Acme" } });
    fireEvent.change(screen.getByLabelText(/Email/), { target: { value: "o@acme.test" } });
    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: "long-enough-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Create my free account" }));
    expect(await screen.findByText("plans page")).toBeInTheDocument();
    const sent = calls.find((c) => c.path === "/auth/signup");
    expect(JSON.parse(String(sent?.init?.body))).toMatchObject({ name: "Acme", email: "o@acme.test", password: "long-enough-1" });
  });

  it("validates before calling the server and explains a duplicate email", async () => {
    const calls = serve({ "/public/plans": json({ plans: [] }), "/auth/signup": json({ detail: "exists" }, 409) });
    at("/signup", <SignupPage />);
    fireEvent.change(screen.getByLabelText(/Business name/), { target: { value: "Acme" } });
    fireEvent.change(screen.getByLabelText(/Email/), { target: { value: "nope" } });
    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: "long-enough-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Create my free account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a valid email address.");
    expect(calls.some((c) => c.path === "/auth/signup")).toBe(false);

    fireEvent.change(screen.getByLabelText(/Email/), { target: { value: "o@acme.test" } });
    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: "long-enough-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Create my free account" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/already exists/));
  });
});

describe("Upgrade prompt", () => {
  it("shows the invitation to a Free account, with the reason after a refused start", () => {
    at(
      "/home",
      <UpgradeBanner access={{ tier: "free", free_credits: 50, credits_remaining: 50 }} attempted />,
    );
    expect(screen.getByText("Your AI Sales Agent is ready to meet your prospects.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Upgrade Your Plan/ }).getAttribute("href")).toBe("/plans");
    expect(screen.getByRole("alert")).toHaveTextContent("Starting your agent needs a paid plan.");
    expect(screen.getByText("50 free credits for preparation")).toBeInTheDocument();
  });

  it("is never shown to a client with a plan, or while the status is unknown", () => {
    const { rerender } = at("/home", <UpgradeBanner access={{ tier: "paid" }} />);
    expect(screen.queryByTestId("upgrade-banner")).toBeNull();
    rerender(
      <MemoryRouter>
        <I18nProvider>
          <UpgradeBanner access={null} />
        </I18nProvider>
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("upgrade-banner")).toBeNull();
  });

  it("lets a Free client pick a plan and tells it the request was received", async () => {
    const calls = serve({
      "/auth/me": json(PROFILE),
      "/account/access": json({ tier: "free", free_credits: 50, credits_remaining: 50 }),
      "/public/plans": json({ plans: PLANS }),
      "/account/plan-request": json({ status: "requested", plan_id: "business" }, 202),
    });
    at("/plans?plan=business", <PlansPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Choose Business" }));
    expect(await screen.findByText(/Request received for the Business plan/)).toBeInTheDocument();
    expect(JSON.parse(String(calls.find((c) => c.path === "/account/plan-request")?.init?.body))).toEqual({ plan_id: "business" });
  });
});
