import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../../i18n";
import { SimulationPage } from "./SimulationPage";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const lane = (id: string, channel: string, authorized = true) => ({
  id,
  position: 1,
  source: { id: id, source: "google_sheets", label: "Sheet", google_sheets: null },
  channel: { channel, label: channel, authorized, problem: null },
  products: [],
  offers_all_products: true,
});

const test = { prospect_id: "inbound-test-amel@x.com", name: "Amel", contact: "amel@x.com", channel: "email", circuit_id: "main", status: "active", messages: 1, replies: 0, last_message_at: "2026-10-28T10:00:00Z" };

function stub(options: { lanes?: unknown[]; start?: () => Response } = {}) {
  const starts: Array<Record<string, unknown>> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/lanes")) return json(200, { version: 1, editable: true, lanes: options.lanes ?? [lane("main", "email")], source_options: [], channel_options: [] });
      if (url.endsWith("/agent-test") && init?.method === "POST") {
        starts.push(JSON.parse(String(init.body)));
        return options.start ? options.start() : json(201, { prospect_id: test.prospect_id, state: "sent", detail: null });
      }
      if (url.endsWith("/agent-test")) return json(200, { tests: [test] });
      if (url.includes("/agent-test/")) return json(200, { test, messages: [{ direction: "outbound", text: "Hello Amel, here is our offer.", sent_at: "2026-10-28T10:00:00Z" }] });
      return json(200, {});
    }),
  );
  return starts;
}

function show() {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <SimulationPage />
      </MemoryRouter>
    </I18nProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("Test your agent", () => {
  it("asks for an email address on an email circuit, starts the test and shows the first message", async () => {
    const starts = stub();
    const user = userEvent.setup();
    show();
    const start = await screen.findByRole("button", { name: "Start the test" });
    expect(start).toBeDisabled();
    await user.type(screen.getByLabelText("Tester’s email address"), "amel@x.com");
    await user.click(start);
    expect(starts).toEqual([{ contact: "amel@x.com", circuit_id: "main" }]);
    expect(await screen.findByText("The first message is on its way")).toBeInTheDocument();
    expect(await screen.findByText("Hello Amel, here is our offer.")).toBeInTheDocument();
  });

  it("asks for a phone number on a WhatsApp circuit", async () => {
    stub({ lanes: [lane("wa", "whatsapp")] });
    show();
    expect(await screen.findByLabelText("Tester’s phone number")).toBeInTheDocument();
  });

  it("says in words that a contact is already a prospect", async () => {
    stub({ start: () => json(409, { detail: { code: "contact_in_use", message: "in use" } }) });
    const user = userEvent.setup();
    show();
    await user.type(await screen.findByLabelText("Tester’s email address"), "amel@x.com");
    await user.click(screen.getByRole("button", { name: "Start the test" }));
    expect(await screen.findByText(/already one of your prospects/)).toBeInTheDocument();
  });

  it("sends the client to the circuits when there is none", async () => {
    stub({ lanes: [] });
    show();
    expect(await screen.findByText("Create a circuit first")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start the test" })).not.toBeInTheDocument();
  });
});
