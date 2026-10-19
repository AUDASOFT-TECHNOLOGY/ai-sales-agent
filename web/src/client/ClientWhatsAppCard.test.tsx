import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nProvider } from "../i18n";
import { sessionInfoOf, type SignupDeps } from "../whatsapp/embeddedSignup";
import { ClientWhatsAppCard } from "./ClientWhatsAppCard";

function status(over: Record<string, unknown> = {}) {
  return {
    state: "not_connected",
    number: "+216 20 000 001",
    verified_name: null,
    status: "action_required",
    message: "Connect your WhatsApp Business account so Revansa can write to your prospects.",
    can_start_conversations: false,
    delivered_7d: 0,
    failed_7d: 0,
    ...over,
  };
}

function json(code: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status: code, headers: { "Content-Type": "application/json" } });
}

function stub(routes: Record<string, () => Response>) {
  const calls: Array<{ key: string; body?: string }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input).replace("/client/v1/channel/whatsapp", "") || "/"}`;
      calls.push({ key, body: init?.body as string | undefined });
      const handler = routes[key];
      if (!handler) throw new Error(`unexpected fetch ${key}`);
      return handler();
    }),
  );
  return calls;
}

const START = { app_id: "999000111222", config_id: "888000111222", api_version: "v21.0", state: "signed.state" };

function sdk(code: string | null): SignupDeps {
  const login = vi.fn((callback: (r: { authResponse?: { code?: string } | null }) => void) =>
    callback({ authResponse: code ? { code } : null }),
  );
  return {
    loadSdk: async () => ({ init: vi.fn(), login }),
    addMessageListener: (listener) => {
      // Meta's window reports the account it chose just before the code is handed over.
      queueMicrotask(() =>
        listener(
          new MessageEvent("message", {
            origin: "https://www.facebook.com",
            data: JSON.stringify({
              type: "WA_EMBEDDED_SIGNUP",
              event: "FINISH",
              data: { waba_id: "111111111111", phone_number_id: "555555555555" },
            }),
          }),
        ),
      );
      return () => undefined;
    },
  };
}

function renderCard(signup: SignupDeps) {
  render(
    <I18nProvider>
      <ClientWhatsAppCard signup={signup} />
    </I18nProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("ClientWhatsAppCard", () => {
  it("connects with Meta's window and sends only the one-time code, the state and Meta's hints", async () => {
    const calls = stub({
      "GET /": () => json(200, status()),
      "POST /start": () => json(200, START),
      "POST /complete": () =>
        json(200, status({ state: "connected", status: "ready", message: null, verified_name: "Alpha SARL" })),
    });
    renderCard(sdk("the-code"));
    await userEvent.click(await screen.findByRole("button", { name: "Connect WhatsApp" }));
    await waitFor(() => expect(screen.getByTestId("whatsapp-state")).toHaveTextContent("Connected"));
    expect(screen.getByTestId("whatsapp-state")).toHaveTextContent("Alpha SARL");
    const complete = calls.find((c) => c.key === "POST /complete");
    expect(JSON.parse(complete?.body ?? "{}")).toEqual({
      state: "signed.state",
      code: "the-code",
      waba_id: "111111111111",
      phone_number_id: "555555555555",
    });
  });

  it("changes nothing when the client closes Meta's window", async () => {
    const calls = stub({ "GET /": () => json(200, status()), "POST /start": () => json(200, START) });
    renderCard(sdk(null));
    await userEvent.click(await screen.findByRole("button", { name: "Connect WhatsApp" }));
    expect(await screen.findByText(/cancelled/i)).toBeInTheDocument();
    expect(calls.map((c) => c.key)).toEqual(["GET /", "POST /start"]);
  });

  it("says in words why a number that is not the client's was refused", async () => {
    stub({
      "GET /": () => json(200, status()),
      "POST /start": () => json(200, START),
      "POST /complete": () => json(409, { detail: "number_mismatch" }),
    });
    renderCard(sdk("the-code"));
    await userEvent.click(await screen.findByRole("button", { name: "Connect WhatsApp" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/not the number in your profile/i);
  });

  it("asks for the number first, and cannot connect without it", async () => {
    stub({ "GET /": () => json(200, status({ number: null })) });
    renderCard(sdk("x"));
    expect(await screen.findByText(/Enter your WhatsApp number/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect WhatsApp" })).toBeDisabled();
  });

  it("offers nothing to press while the platform is not set up", async () => {
    stub({ "GET /": () => json(200, status({ state: "unavailable" })) });
    renderCard(sdk("x"));
    expect(await screen.findByText(/Revansa has to finish/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Connect WhatsApp/ })).toBeNull();
  });

  it("asks before disconnecting, then shows the account as not connected", async () => {
    stub({
      "GET /": () => json(200, status({ state: "connected", status: "ready", message: null })),
      "POST /disconnect": () => json(200, status()),
    });
    renderCard(sdk("x"));
    await userEvent.click(await screen.findByRole("button", { name: "Disconnect" }));
    await userEvent.click(screen.getByRole("button", { name: "Yes, disconnect" }));
    await waitFor(() => expect(screen.getByTestId("whatsapp-state")).toHaveTextContent("Not connected"));
  });
});

describe("sessionInfoOf", () => {
  const message = (origin: string, data: unknown) => new MessageEvent("message", { origin, data: JSON.stringify(data) });

  it("trusts only Meta's origin and only the WhatsApp signup event", () => {
    const body = { type: "WA_EMBEDDED_SIGNUP", event: "FINISH", data: { waba_id: "111111111111" } };
    expect(sessionInfoOf(message("https://www.facebook.com", body))?.wabaId).toBe("111111111111");
    expect(sessionInfoOf(message("https://evil.example", body))).toBeNull();
    expect(sessionInfoOf(message("https://facebook.com.evil.example", body))).toBeNull();
    expect(sessionInfoOf(message("http://www.facebook.com", body))).toBeNull();
    expect(sessionInfoOf(message("https://www.facebook.com", { ...body, type: "OTHER" }))).toBeNull();
  });

  it("keeps an id only if it looks like one", () => {
    const body = { type: "WA_EMBEDDED_SIGNUP", event: "FINISH", data: { waba_id: "<script>", phone_number_id: "5555555555" } };
    const info = sessionInfoOf(message("https://web.facebook.com", body));
    expect(info?.wabaId).toBeUndefined();
    expect(info?.phoneNumberId).toBe("5555555555");
  });
});
