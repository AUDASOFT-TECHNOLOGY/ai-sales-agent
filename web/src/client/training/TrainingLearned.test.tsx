/** The training page shows what the agent learned in its overview, without adding a tab or a setting. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../../i18n";
import { ClientAuthProvider } from "../ClientAuthContext";
import { TrainingPage } from "./TrainingPage";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const COMMERCIAL = { communication: { languages: { default: "en", supported: ["en"] } }, catalog: { products: [{ id: "alpha", name: "Alpha Course" }] } };

function stub(learning: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input).replace(/^.*\/client\/v1/, "").split("?")[0]!;
      if (path === "/settings") return json(200, { version: 1, settings: { commercial: COMMERCIAL }, updated_at: null, updated_by: null });
      if (path === "/auth/me") return json(200, { client_id: "naga", display_name: "Naga", whatsapp_number: "+21620000000" });
      if (path === "/learning") return json(200, learning);
      return json(404, {});
    }),
  );
}

const show = () =>
  render(
    <I18nProvider>
      <MemoryRouter>
        <ClientAuthProvider>
          <TrainingPage />
        </ClientAuthProvider>
      </MemoryRouter>
    </I18nProvider>,
  );

afterEach(() => vi.unstubAllGlobals());

const openAuto = () => userEvent.setup().click(screen.getByRole("tab", { name: /It learns by itself/ }));

describe("training page: what you teach, and what the agent learns by itself", () => {
  it("opens on what the client teaches, with the automatic learning one click away and apart", async () => {
    stub({ counts: { studied: 14, achieved: 6, unfavourable: 8, needed: 5 }, notes: [] });
    show();
    expect(await screen.findByRole("tab", { name: /You teach it/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /It learns by itself/ })).toHaveAttribute("aria-selected", "false");
    expect(screen.queryByTestId("training-learned")).toBeNull();
    expect(screen.getByTestId("training-readiness")).toBeInTheDocument();
  });

  it("shows only what the agent learned on its own, and none of the client's own training", async () => {
    stub({ counts: { studied: 14, achieved: 6, unfavourable: 8, needed: 5 }, notes: [{ id: "short_messages", code: "short_messages", kind: "do", state: "active", evidence: "medium", cases: 6, against: 0, product: null, confirmed: false, off_by: null, reason: "" }] });
    show();
    await screen.findByTestId("training-readiness");
    await openAuto();
    const card = await screen.findByTestId("training-learned");
    expect(within(card).getByText("Short, easy-to-read messages")).toBeInTheDocument();
    expect(screen.queryByTestId("training-readiness")).toBeNull();
    expect(screen.queryByRole("button", { name: /Save/ })).toBeNull();
  });

  it("says how long until it learns", async () => {
    stub({ counts: { studied: 0, achieved: 0, unfavourable: 0, needed: 5 }, notes: [] });
    show();
    await screen.findByTestId("training-readiness");
    await openAuto();
    expect(await screen.findByText(/more finished conversation/)).toBeInTheDocument();
  });
});
