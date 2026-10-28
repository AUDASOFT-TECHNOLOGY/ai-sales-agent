import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { AppShell } from "../shell/AppShell";
import { ClientAuthProvider } from "./ClientAuthContext";
import { ClientLoginPage } from "./ClientLoginPage";
import { ClientProfilePage } from "./ClientProfilePage";
import { ClientRoute } from "./ClientRoute";

const PROFILE = {
  name: "Acme Corp",
  login: "acme.admin",
  whatsapp_number: "+216 22 768 307",
  status: "active",
  must_change_password: false,
  whatsapp_reconnect_required: false,
};

interface Stub {
  me?: "authenticated" | "anonymous";
  login?: 200 | 401 | 403 | 429 | 500 | "network";
  password?: 200 | 400 | 429;
  /** Answer to PATCH /profile: 200 applies the changes; otherwise the refusal and its message. */
  save?: 200 | [400 | 409 | 500, string];
}

/** Answers `/client/v1/*` the way the real router does (src/sales_app/api/client.py). */
function stubFetch(opts: Stub = {}) {
  let authenticated = (opts.me ?? "anonymous") === "authenticated";
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/auth/me")) {
      return authenticated ? json(200, PROFILE) : json(401, { detail: "not authenticated" });
    }
    if (url.endsWith("/auth/login")) {
      const status = opts.login ?? 200;
      if (status === "network") throw new TypeError("offline");
      if (status === 200) {
        authenticated = true;
        return json(200, PROFILE);
      }
      return json(status, { detail: "nope" });
    }
    if (url.endsWith("/auth/logout")) {
      authenticated = false;
      return json(200, { status: "ok" });
    }
    if (url.endsWith("/profile") && init?.method === "PATCH") {
      const save = opts.save ?? 200;
      if (save !== 200) return json(save[0], { detail: save[1] });
      const changes = JSON.parse(String(init.body)) as Record<string, string>;
      return json(200, { ...PROFILE, ...changes, whatsapp_reconnect_required: "whatsapp_number" in changes });
    }
    if (url.endsWith("/profile/password")) {
      const status = opts.password ?? 200;
      return status === 200 ? json(200, PROFILE) : json(status, { detail: "nope" });
    }
    if (init?.method === undefined && url.includes("/admin/")) throw new Error(`client UI called the admin: ${url}`);
    throw new Error(`unexpected fetch ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** Mirrors the client route tree of App.tsx: /login public, everything else behind ClientRoute. */
function renderApp(path: string) {
  render(
    <I18nProvider>
      <MemoryRouter initialEntries={[path]}>
        <ClientAuthProvider>
          <Routes>
            <Route path="/login" element={<ClientLoginPage />} />
            <Route
              element={
                <ClientRoute>
                  <Outlet />
                </ClientRoute>
              }
            >
              <Route element={<AppShell />}>
                <Route index element={<h2>Home page</h2>} />
                <Route path="profile" element={<ClientProfilePage />} />
                <Route path="account/profile" element={<ClientProfilePage />} />
              </Route>
            </Route>
          </Routes>
        </ClientAuthProvider>
      </MemoryRouter>
    </I18nProvider>,
  );
}

async function fillLogin(user: ReturnType<typeof userEvent.setup>, login = "acme.admin", password = "temp-pass-123") {
  await user.type(screen.getByLabelText("Login"), login);
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "Login" }));
}

beforeEach(() => {
  vi.stubGlobal("scrollTo", vi.fn()); // AppShell scrolls to top on navigation; jsdom does not implement it
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Client login", () => {
  it("shows the Login and Password fields and a Login button", async () => {
    stubFetch();
    renderApp("/login");
    expect(await screen.findByLabelText("Login")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: "Login" })).toBeInTheDocument();
  });

  it("sends the credentials and redirects to the client space on success", async () => {
    const fetchMock = stubFetch();
    const user = userEvent.setup();
    renderApp("/login");
    await screen.findByLabelText("Login");
    await fillLogin(user);
    expect(await screen.findByRole("heading", { name: "Home page" })).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/auth/login"))!;
    expect(call[1]?.credentials).toBe("include");
    expect(JSON.parse(String(call[1]?.body))).toEqual({ login: "acme.admin", password: "temp-pass-123" });
  });

  it("shows a loading state while the request is in flight", async () => {
    stubFetch();
    const base = globalThis.fetch;
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/auth/login")) await gate;
      return base(input, init);
    });
    const user = userEvent.setup();
    renderApp("/login");
    await screen.findByLabelText("Login");
    await fillLogin(user);
    expect(screen.getByRole("button", { name: "Login" })).toBeDisabled();
    release();
    expect(await screen.findByRole("heading", { name: "Home page" })).toBeInTheDocument();
  });

  it.each([
    [401, "Incorrect login or password."],
    [403, "This account is inactive. Contact Revansa."],
    [429, "Too many attempts. Try again in a few minutes."],
    [500, "Something went wrong. Check your connection and try again."],
    ["network", "Something went wrong. Check your connection and try again."],
  ] as const)("shows the right error for %s and stays on the form", async (login, message) => {
    stubFetch({ login });
    const user = userEvent.setup();
    renderApp("/login");
    await screen.findByLabelText("Login");
    await fillLogin(user);
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByLabelText("Password")).toHaveValue(""); // the password is cleared
    expect(screen.queryByRole("heading", { name: "Home page" })).not.toBeInTheDocument();
  });

  it("does not reveal whether the login exists: one message for any bad credentials", async () => {
    stubFetch({ login: 401 });
    const user = userEvent.setup();
    renderApp("/login");
    await screen.findByLabelText("Login");
    await fillLogin(user, "ghost.login", "whatever-123");
    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect login or password.");
  });

  it("skips the form when a session already exists", async () => {
    stubFetch({ me: "authenticated" });
    renderApp("/login");
    expect(await screen.findByRole("heading", { name: "Home page" })).toBeInTheDocument();
  });
});

describe("Client route protection", () => {
  it("redirects an anonymous visitor from a protected page to the login", async () => {
    stubFetch({ me: "anonymous" });
    renderApp("/profile");
    expect(await screen.findByLabelText("Login")).toBeInTheDocument();
    expect(screen.queryByText("Change password")).not.toBeInTheDocument();
  });

  it("redirects an anonymous visitor from the client home to the login", async () => {
    stubFetch({ me: "anonymous" });
    renderApp("/");
    expect(await screen.findByLabelText("Login")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Home page" })).not.toBeInTheDocument();
  });

  it("shows a brief status, not the page, while the session is being checked", () => {
    vi.stubGlobal("fetch", () => new Promise(() => {}));
    renderApp("/profile");
    expect(screen.getByRole("status")).toHaveTextContent("Checking your session…");
    expect(screen.queryByText("Change password")).not.toBeInTheDocument();
  });

  it("lets an authenticated client in", async () => {
    stubFetch({ me: "authenticated" });
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Home page" })).toBeInTheDocument();
  });
});

describe("Client profile", () => {
  it("shows name, login, WhatsApp number and status of the logged-in client", async () => {
    stubFetch({ me: "authenticated" });
    renderApp("/profile");
    expect(await screen.findByRole("heading", { level: 2, name: "Profile" })).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Acme Corp");
    expect(screen.getByLabelText("Login")).toHaveValue("acme.admin");
    expect(screen.getByLabelText("WhatsApp number")).toHaveValue("+216 22 768 307");
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("makes name, login and WhatsApp number editable but not the status", async () => {
    stubFetch({ me: "authenticated" });
    renderApp("/profile");
    await screen.findByRole("heading", { level: 2, name: "Profile" });
    for (const label of ["Name", "Login", "WhatsApp number"]) {
      expect(screen.getByLabelText(label)).toBeEnabled();
    }
    expect(screen.queryByLabelText("Status")).not.toBeInTheDocument(); // not an input
    expect(screen.getByText("Only Revansa can activate or deactivate your account.")).toBeInTheDocument();
  });

  it("shows no Meta credential or technical secret", async () => {
    stubFetch({ me: "authenticated" });
    renderApp("/profile");
    await screen.findByRole("heading", { level: 2, name: "Profile" });
    for (const word of [/access token/i, /phone number id/i, /app secret/i, /verify token/i]) {
      expect(screen.queryByText(word)).not.toBeInTheDocument();
    }
  });

  it("warns about a temporary password", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json(200, { ...PROFILE, must_change_password: true })),
    );
    renderApp("/profile");
    expect(await screen.findByText(/temporary password/i)).toBeInTheDocument();
  });
});

describe("Edit profile", () => {
  const patchCalls = (f: ReturnType<typeof stubFetch>) => f.mock.calls.filter(([, init]) => init?.method === "PATCH");

  async function openProfile(stub: Stub = {}) {
    const fetchMock = stubFetch({ me: "authenticated", ...stub });
    const user = userEvent.setup();
    renderApp("/profile");
    await screen.findByRole("heading", { level: 2, name: "Profile" });
    return { fetchMock, user };
  }
  const save = (user: ReturnType<typeof userEvent.setup>) => user.click(screen.getByRole("button", { name: "Save changes" }));

  it("keeps Save disabled until something changes", async () => {
    const { user } = await openProfile();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    await user.type(screen.getByLabelText("Name"), "x");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
  });

  it("saves a new name and sends only that field", async () => {
    const { fetchMock, user } = await openProfile();
    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "Acme Industries");
    await save(user);
    expect(await screen.findByText("Your profile has been saved.")).toBeInTheDocument();
    expect(JSON.parse(String(patchCalls(fetchMock)[0]![1]?.body))).toEqual({ name: "Acme Industries" });
    expect(screen.getByLabelText("Name")).toHaveValue("Acme Industries");
  });

  it("saves a new login", async () => {
    const { fetchMock, user } = await openProfile();
    await user.clear(screen.getByLabelText("Login"));
    await user.type(screen.getByLabelText("Login"), "acme.new");
    await save(user);
    expect(await screen.findByText("Your profile has been saved.")).toBeInTheDocument();
    expect(JSON.parse(String(patchCalls(fetchMock)[0]![1]?.body))).toEqual({ login: "acme.new" });
  });

  it("never sends the status, whatever is edited", async () => {
    const { fetchMock, user } = await openProfile();
    await user.type(screen.getByLabelText("Name"), "!");
    await save(user);
    await screen.findByText("Your profile has been saved.");
    expect(JSON.parse(String(patchCalls(fetchMock)[0]![1]?.body))).not.toHaveProperty("status");
  });

  it("says a changed WhatsApp number is not connected yet", async () => {
    const { user } = await openProfile();
    expect(screen.queryByText(/not connected yet/i)).not.toBeInTheDocument();
    await user.clear(screen.getByLabelText("WhatsApp number"));
    await user.type(screen.getByLabelText("WhatsApp number"), "+33 6 12 34 56 78");
    await save(user);
    expect(await screen.findByText(/not connected yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/connected$/i)).not.toBeInTheDocument(); // never claims "Connected"
  });

  it.each([
    [[409, "this login is already used"], "This login is already used. Choose another one.", "Login"],
    [[409, "this WhatsApp number already belongs to another client"], "This WhatsApp number already belongs to another account.", "WhatsApp number"],
    [[400, "WhatsApp number must be a phone number with 8 to 15 digits"], "Enter a valid WhatsApp number with 8 to 15 digits, e.g. +216 22 768 307.", "WhatsApp number"],
    [[500, "boom"], "Something went wrong. Check your connection and try again.", "Name"],
  ] as const)("shows the refusal %j", async (refusal, message, field) => {
    const { user } = await openProfile({ save: [...refusal] as [400 | 409 | 500, string] });
    await user.type(screen.getByLabelText(field), "9");
    await save(user);
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByText("Your profile has been saved.")).not.toBeInTheDocument();
  });
});

describe("Change password", () => {
  async function openForm() {
    const user = userEvent.setup();
    renderApp("/profile");
    await screen.findByRole("heading", { name: "Change password" });
    return user;
  }
  async function fill(user: ReturnType<typeof userEvent.setup>, current: string, next: string, confirm: string) {
    await user.type(screen.getByLabelText("Current password"), current);
    await user.type(screen.getByLabelText("New password"), next);
    await user.type(screen.getByLabelText("Confirm new password"), confirm);
    await user.click(screen.getByRole("button", { name: "Change password" }));
  }
  const passwordCalls = (f: ReturnType<typeof stubFetch>) => f.mock.calls.filter(([url]) => String(url).endsWith("/profile/password"));

  it("sends the three fields and confirms, clearing the form", async () => {
    const fetchMock = stubFetch({ me: "authenticated" });
    const user = await openForm();
    await fill(user, "old-pass-123", "new-pass-456", "new-pass-456");
    expect(await screen.findByText("Your password has been changed.")).toBeInTheDocument();
    expect(JSON.parse(String(passwordCalls(fetchMock)[0]![1]?.body))).toEqual({
      current_password: "old-pass-123",
      new_password: "new-pass-456",
      confirm_password: "new-pass-456",
    });
    expect(screen.getByLabelText("Current password")).toHaveValue("");
    expect(screen.getByLabelText("New password")).toHaveValue("");
  });

  it("refuses a different confirmation without calling the server", async () => {
    const fetchMock = stubFetch({ me: "authenticated" });
    const user = await openForm();
    await fill(user, "old-pass-123", "new-pass-456", "other-pass-789");
    expect((await screen.findAllByText("The confirmation does not match the new password.")).length).toBeGreaterThan(0);
    expect(passwordCalls(fetchMock)).toHaveLength(0);
  });

  it("refuses a too-short new password without calling the server", async () => {
    const fetchMock = stubFetch({ me: "authenticated" });
    const user = await openForm();
    await fill(user, "old-pass-123", "short", "short");
    expect((await screen.findAllByText("The new password must have at least 8 characters.")).length).toBeGreaterThan(0);
    expect(passwordCalls(fetchMock)).toHaveLength(0);
  });

  it("shows an error when the current password is wrong", async () => {
    stubFetch({ me: "authenticated", password: 400 });
    const user = await openForm();
    await fill(user, "wrong-pass-000", "new-pass-456", "new-pass-456");
    expect(await screen.findByRole("alert")).toHaveTextContent("The current password is incorrect.");
    expect(screen.queryByText("Your password has been changed.")).not.toBeInTheDocument();
  });
});

describe("Client logout", () => {
  it("logs out from the account menu and returns to the login", async () => {
    const fetchMock = stubFetch({ me: "authenticated" });
    const user = userEvent.setup();
    renderApp("/");
    await screen.findByRole("heading", { name: "Home page" });
    await user.click(screen.getByRole("button", { name: "Account menu" }));
    await user.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByLabelText("Login")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/auth/logout"))).toBe(true));
    expect(screen.queryByRole("heading", { name: "Home page" })).not.toBeInTheDocument();
  });

  it("still leaves when the logout request fails", async () => {
    const fetchMock = stubFetch({ me: "authenticated" });
    const user = userEvent.setup();
    renderApp("/");
    await screen.findByRole("heading", { name: "Home page" });
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => {
      if (String(input).endsWith("/auth/logout")) throw new TypeError("offline");
      return base(input, init);
    });
    await user.click(screen.getByRole("button", { name: "Account menu" }));
    await user.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByLabelText("Login")).toBeInTheDocument();
  });

  it("opens the profile from the account menu", async () => {
    stubFetch({ me: "authenticated" });
    const user = userEvent.setup();
    renderApp("/");
    await screen.findByRole("heading", { name: "Home page" });
    await user.click(screen.getByRole("button", { name: "Account menu" }));
    await user.click(await screen.findByRole("link", { name: "My account" }));
    expect(await screen.findByRole("heading", { name: "Change password" })).toBeInTheDocument();
  });
});
