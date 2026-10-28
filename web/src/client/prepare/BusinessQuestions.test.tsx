import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../../i18n";
import { ClientAuthProvider } from "../ClientAuthContext";
import { OfferProvider } from "../offer/OfferLayout";
import { BusinessQuestions } from "./BusinessQuestions";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function stub() {
  const writes: Array<Record<string, unknown>> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return json(200, { name: "Acme", login: "a", whatsapp_number: "+1", status: "active", must_change_password: false, whatsapp_reconnect_required: false });
      if (url.endsWith("/settings") && init?.method === "PUT") {
        writes.push(JSON.parse(String(init.body)));
        return json(200, { version: 2, settings: JSON.parse(String(init.body)).settings, updated_at: null, updated_by: "client" });
      }
      if (url.endsWith("/settings")) return json(200, { version: 1, settings: { commercial: {} }, updated_at: null, updated_by: null });
      return json(200, {});
    }),
  );
  return writes;
}

afterEach(() => vi.unstubAllGlobals());

describe("Business questionnaire on a new account", () => {
  it("writes the name with the languages the server requires, from the interface language", async () => {
    const writes = stub();
    const user = userEvent.setup();
    render(
      <I18nProvider>
        <MemoryRouter>
          <ClientAuthProvider>
            <OfferProvider>
              <BusinessQuestions />
            </OfferProvider>
          </ClientAuthProvider>
        </MemoryRouter>
      </I18nProvider>,
    );
    await user.type(await screen.findByLabelText("Business name"), "Naga");
    await user.click(screen.getByRole("button", { name: /Continue/ }));
    await screen.findByText(/What do you do/);
    expect(writes).toHaveLength(1);
    const commercial = (writes[0]!.settings as Record<string, unknown>).commercial as { business: { name: string }; communication: { languages: { supported: string[]; default: string } } };
    expect(commercial.business.name).toBe("Naga");
    expect(commercial.communication.languages.supported).toHaveLength(1);
    expect(commercial.communication.languages.default).toBe(commercial.communication.languages.supported[0]);
  });
});
