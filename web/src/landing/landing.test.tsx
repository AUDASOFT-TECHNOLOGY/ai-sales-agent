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

  it("offers the free account next to the paid plans, and says how a plan is paid", async () => {
    serve({ "/public/plans": json({ plans: PLANS }) });
    at("/", <LandingPage />);
    const free = await screen.findByTestId("landing-plan-free");
    expect(free.querySelector("a")!.getAttribute("href")).toBe("/signup");
    expect(screen.getAllByTestId(/^landing-plan-/)).toHaveLength(4);
    expect(screen.getByText(/Pay by bank transfer/)).toBeInTheDocument();
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

  it("states the hero, the Meta billing caveat and that the example is fictional", async () => {
    serve({ "/public/plans": json({ plans: PLANS }) });
    at("/", <LandingPage />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("It prospects, follows up and closes. Without ever stopping.");
    expect(screen.getAllByText("Start for Free").length).toBeGreaterThan(0);
    expect(await screen.findByText(/billed separately/)).toBeInTheDocument();
    expect(screen.getAllByText(/Example · fictional data/).length).toBeGreaterThan(0);
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


  it("walks a Free client through the bank transfer: details, amount, receipt, then waiting for review", async () => {
    const requests: unknown[] = [];
    const calls = serve({
      "/auth/me": json(PROFILE),
      "/account/access": json({ tier: "free", free_credits: 50, credits_remaining: 50 }),
      "/public/plans": json({ plans: PLANS }),
      "/account/bank-details?locale=en": json({ title: "Our bank", body: "IBAN TN59 0000" }),
      "/account/payment-requests": (init) =>
        init?.method === "POST"
          ? (requests.push(1), new Response(JSON.stringify({ request_id: 1, status: "pending" }), { status: 201 }))
          : new Response(JSON.stringify({ requests: requests.length ? [{ request_id: 1, plan_id: "business", plan_name: "Business", amount: "129.00", currency: "USD", reference: "", status: "pending", created_at: "2026-10-30T10:00:00Z", decided_at: null, reason: null }] : [] })),
    });
    at("/plans?plan=business", <PlansPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Pay for Business" }));
    expect(await screen.findByText("IBAN TN59 0000")).toBeInTheDocument();
    expect(screen.getByText("Amount to transfer: $129")).toBeInTheDocument();

    const file = new File(["%PDF-1.4"], "receipt.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText(/Receipt \(PDF/), { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText(/Transfer reference/), { target: { value: "VIR-42" } });
    fireEvent.click(screen.getByRole("button", { name: "Send my receipt" }));
    expect(await screen.findByText(/Receipt received for the Business plan/)).toBeInTheDocument();
    const sent = JSON.parse(String(calls.find((c) => c.init?.method === "POST")?.init?.body));
    expect(sent).toMatchObject({ plan_id: "business", reference: "VIR-42" });
    expect(sent.receipt).toMatch(/^data:application\/pdf;base64,/);
  });

  it("refuses a file that is not a PDF or an image before sending anything", async () => {
    const calls = serve({
      "/auth/me": json(PROFILE),
      "/account/access": json({ tier: "free" }),
      "/public/plans": json({ plans: PLANS }),
      "/account/bank-details?locale=en": json({ title: "Bank", body: "x" }),
      "/account/payment-requests": json({ requests: [] }),
    });
    at("/plans", <PlansPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Pay for Starter" }));
    fireEvent.change(await screen.findByLabelText(/Receipt \(PDF/), { target: { files: [new File(["x"], "a.exe", { type: "application/x-msdownload" })] } });
    fireEvent.click(screen.getByRole("button", { name: "Send my receipt" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/PDF, PNG, JPEG or WebP/);
    expect(calls.some((c) => c.init?.method === "POST")).toBe(false);
  });

  it("links the footer only to the pages the administrator wrote", async () => {
    serve({ "/public/plans": json({ plans: PLANS }), "/public/pages": json({ pages: ["privacy"] }) });
    at("/", <LandingPage />);
    const privacy = await screen.findByRole("link", { name: "Privacy Policy" });
    expect(privacy.getAttribute("href")).toBe("/page/privacy");
    expect(screen.queryByRole("link", { name: "Terms of Service" })).toBeNull();
    expect(screen.getByText("Terms of Service")).toBeInTheDocument(); // shown as coming soon, not a link
  });
});
