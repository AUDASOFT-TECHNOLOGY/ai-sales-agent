import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../../i18n";
import { ConnectionNeeded, SourceConnectionNeeded } from "./ConnectionNeeded";

function stub(path: string, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (!String(input).endsWith(path)) throw new Error(`unexpected fetch ${String(input)}`);
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    }),
  );
}

function renderIt(ui: React.ReactNode) {
  return render(
    <I18nProvider>
      <MemoryRouter>{ui}</MemoryRouter>
    </I18nProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("ConnectionNeeded", () => {
  it("says a configuration is needed and links to the Connections screen", () => {
    renderIt(<ConnectionNeeded />);
    expect(screen.getByRole("link")).toHaveAttribute("href", "/connections");
  });

  it("is shown for a HubSpot source that is not connected", async () => {
    stub("/hubspot-access", { state: "not_connected", account: null, message: "" });
    renderIt(<SourceConnectionNeeded provider="hubspot" />);
    expect(await screen.findByRole("link")).toHaveAttribute("href", "/connections");
  });

  it("is hidden once the source is connected", async () => {
    stub("/sheets-access", { state: "connected", address: "a@b.c", spreadsheet_saved: true, message: "" });
    const { container } = renderIt(<SourceConnectionNeeded provider="google_sheets" />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    await waitFor(() => expect(container.querySelector("a")).toBeNull());
  });

  it("asks nothing of a spreadsheet shared with a service account", () => {
    const { container } = renderIt(<SourceConnectionNeeded provider="google_sheets" shareWith="bot@x.iam" />);
    expect(container.querySelector("a")).toBeNull();
  });
});
