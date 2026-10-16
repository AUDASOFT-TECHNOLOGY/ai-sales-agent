import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../i18n";
import { ClientSettingsPage } from "./ClientSettingsPage";

const logout = vi.fn(async () => undefined);
vi.mock("./ClientAuthContext", () => ({ useClientAuth: () => ({ logout }) }));

type Json = Record<string, unknown>;

/** What `GET /client/v1/settings` answers: the editable part, `commercial` being the Brain document. */
function settingsState(over: Json = {}) {
  return {
    version: 4,
    updated_at: "2026-10-10T09:00:00Z",
    updated_by: "administrator",
    settings: {
      timezone: "Africa/Tunis",
      opt_out_keywords: ["stop", "unsubscribe"],
      schedule: { daily_at: "09:00", days: ["mon", "tue"] },
      commercial: {
        business: { name: "Acme Solar", website: "https://acme.example" },
        communication: { languages: { default: "fr", supported: ["fr", "ar"] }, tone: { style: "warm" } },
        catalog: {
          products: [
            {
              id: "kit",
              status: "active",
              name: { fr: "Kit solaire", ar: "طقم" },
              summary: "Panneaux",
              pricing: { model: "fixed", amount: 900, currency: "TND" },
            },
          ],
        },
        strategy_like: { keep: "me" },
      },
      ...over,
    },
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Handler = (init: RequestInit | undefined) => Response | Promise<Response>;

/** Only fetch is simulated (the server is not reachable from a unit test). */
function stub(routes: Record<string, Handler>) {
  const calls: Array<{ key: string; body: unknown }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input)}`;
      calls.push({ key, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const handler = routes[key];
      if (!handler) throw new Error(`unexpected fetch ${key}`);
      return handler(init);
    }),
  );
  return calls;
}

function renderPage() {
  render(
    <I18nProvider>
      <MemoryRouter>
        <ClientSettingsPage />
      </MemoryRouter>
    </I18nProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  logout.mockClear();
});

const PUT = "PUT /client/v1/settings";

describe("ClientSettingsPage", () => {
  it("shows the settings the server holds, and calls nothing else", async () => {
    const calls = stub({ "GET /client/v1/settings": () => json(200, settingsState()) });
    renderPage();
    expect(await screen.findByDisplayValue("Acme Solar")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Africa/Tunis")).toBeInTheDocument();
    expect(screen.getByDisplayValue("stop, unsubscribe")).toBeInTheDocument();
    expect(screen.getByTestId("product-kit")).toHaveTextContent("kit");
    expect(screen.getByDisplayValue("Kit solaire")).toBeInTheDocument(); // the default language's version
    expect(screen.getByTestId("settings-version")).toHaveTextContent(/Version\D+4/);
    expect(calls.map((c) => c.key)).toEqual(["GET /client/v1/settings"]);
  });

  it("saves on the version it was based on, keeps what it does not edit, and confirms only after the answer", async () => {
    const saved = { ...settingsState({ timezone: "Europe/Paris" }), version: 5, updated_by: "client" };
    let release: (r: Response) => void = () => undefined;
    const calls = stub({
      "GET /client/v1/settings": () => json(200, settingsState()),
      [PUT]: () => new Promise<Response>((resolve) => (release = resolve)),
    });
    const user = userEvent.setup();
    renderPage();
    const tz = await screen.findByLabelText("Time zone");
    await user.clear(tz);
    await user.type(tz, "Europe/Paris");
    const name = screen.getByDisplayValue("Kit solaire");
    await user.clear(name);
    await user.type(name, "Kit solaire pro");
    await user.click(screen.getByRole("button", { name: "Save settings" }));

    // Nothing is announced while the server has not answered.
    await waitFor(() => expect(calls.some((c) => c.key === PUT)).toBe(true));
    expect(screen.queryByText(/Saved\./)).not.toBeInTheDocument();

    release(json(200, saved));
    expect(await screen.findByText(/Saved\./)).toBeInTheDocument();
    expect(screen.getByTestId("settings-version")).toHaveTextContent(/Version\D+5/);

    const put = calls.find((c) => c.key === PUT)!.body as {
      expected_version: number;
      settings: { timezone: string; commercial: { catalog: { products: Json[] }; strategy_like: unknown } };
    };
    expect(put.expected_version).toBe(4);
    expect(put.settings.timezone).toBe("Europe/Paris");
    const product = put.settings.commercial.catalog.products[0]!;
    expect(product.name).toEqual({ fr: "Kit solaire pro", ar: "طقم" }); // the other language is kept
    expect(product.pricing).toEqual({ model: "fixed", amount: 900, currency: "TND" }); // untouched
    expect(put.settings.commercial.strategy_like).toEqual({ keep: "me" });
  });

  it("shows the server's reasons when it refuses, and never claims a save", async () => {
    stub({
      "GET /client/v1/settings": () => json(200, settingsState()),
      [PUT]: () => json(422, { detail: ["brain.settings.business.name: must not be blank", "brain.settings.communication.languages: bad"] }),
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Save settings" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Nothing was saved");
    expect(alert).toHaveTextContent("business.name: must not be blank");
    expect(screen.queryByText(/Saved\./)).not.toBeInTheDocument();
    expect(screen.getByTestId("settings-version")).toHaveTextContent(/Version\D+4/); // unchanged
  });

  it("reloads the server's settings after a conflict and says the edits were not saved", async () => {
    let reads = 0;
    stub({
      "GET /client/v1/settings": () => json(200, reads++ === 0 ? settingsState() : { ...settingsState(), version: 6 }),
      [PUT]: () => json(409, { detail: "the settings changed since they were loaded; reload and retry" }),
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Save settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("your edits were not saved");
    await waitFor(() => expect(screen.getByTestId("settings-version")).toHaveTextContent(/Version\D+6/));
    expect(screen.queryByText(/Saved\./)).not.toBeInTheDocument();
  });

  it("shows the error and a retry when the settings cannot be loaded", async () => {
    let reads = 0;
    stub({
      "GET /client/v1/settings": () => (reads++ === 0 ? json(503, { detail: "settings are temporarily unavailable" }) : json(200, settingsState())),
    });
    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
    expect(screen.queryByLabelText("Business name")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByDisplayValue("Acme Solar")).toBeInTheDocument();
  });

  it("signs the client out when the session has ended", async () => {
    stub({ "GET /client/v1/settings": () => json(401, { detail: "not authenticated" }) });
    renderPage();
    await waitFor(() => expect(logout).toHaveBeenCalled());
  });

  it("adds a product to the document that is saved, refusing a duplicate identifier", async () => {
    const calls = stub({
      "GET /client/v1/settings": () => json(200, settingsState()),
      [PUT]: () => json(200, settingsState()),
    });
    const user = userEvent.setup();
    renderPage();
    await user.type(await screen.findByLabelText("Identifier"), "kit");
    await user.type(screen.getByLabelText("Name of the new product"), "Dup");
    await user.type(screen.getByLabelText("Short description of the new product"), "Dup");
    await user.click(screen.getByRole("button", { name: "Add to the list" }));
    expect(await screen.findByText("A product already has this identifier.")).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Identifier"));
    await user.type(screen.getByLabelText("Identifier"), "pump");
    await user.click(screen.getByRole("button", { name: "Add to the list" }));
    expect(await screen.findByTestId("product-pump")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() => expect(calls.some((c) => c.key === PUT)).toBe(true));
    const put = calls.find((c) => c.key === PUT)!.body as { settings: { commercial: { catalog: { products: Json[] } } } };
    expect(put.settings.commercial.catalog.products.map((p) => p.id)).toEqual(["kit", "pump"]);
  });
});
