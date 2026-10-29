import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../../i18n";
import { ClientAuthProvider } from "../ClientAuthContext";
import { TrainingPage } from "./TrainingPage";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const TEST = { prospect_id: "inbound-test-amel", name: "Amel", contact: "amel@x.com", channel: "email", circuit_id: "main", status: "active", messages: 1, replies: 0, last_message_at: "2026-10-28T10:00:00Z" };

const COMMERCIAL = {
  communication: { languages: { default: "en", supported: ["en"] } },
  catalog: { products: [{ id: "alpha", name: "Alpha Course" }, { id: "beta", name: "Beta Course" }] },
};

function stub(options: { commercial?: Record<string, unknown>; replies?: Array<string | null> } = {}) {
  const said: string[] = [];
  const replies = [...(options.replies ?? [])];
  const saves: Array<{ settings: { commercial: { communication: Record<string, unknown> } } }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^.*\/client\/v1/, "").split("?")[0]!;
      const method = init?.method ?? "GET";
      if (path === "/settings" && method === "PUT") {
        const body = JSON.parse(String(init!.body));
        saves.push(body);
        return json(200, { version: 2, settings: body.settings, updated_at: null, updated_by: "client" });
      }
      if (path === "/settings") return json(200, { version: 1, settings: { commercial: options.commercial ?? COMMERCIAL }, updated_at: null, updated_by: null });
      if (path === "/auth/me") return json(200, { client_id: "naga", display_name: "Naga", whatsapp_number: "+21620000000" });
      if (path === "/agent-test") return json(200, { tests: options.replies ? [TEST] : [] });
      if (path === `/agent-test/${TEST.prospect_id}/rehearse`) {
        said.push(JSON.parse(String(init!.body)).text);
        return json(200, { decision: "send_message", reply: replies.shift() ?? null });
      }
      if (path === `/agent-test/${TEST.prospect_id}`) return json(200, { test: TEST, messages: [{ direction: "outbound", text: "Hello Amel", sent_at: "2026-10-28T10:00:00Z" }] });
      return json(404, {});
    }),
  );
  return { saves, said };
}

function show() {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <ClientAuthProvider>
          <TrainingPage />
        </ClientAuthProvider>
      </MemoryRouter>
    </I18nProvider>,
  );
}

const tab = (name: string) => screen.findByRole("tab", { name });
afterEach(() => vi.unstubAllGlobals());

describe("Train your agent", () => {
  it("opens on how ready the agent is, with what to do first", async () => {
    stub();
    show();
    expect(await screen.findByTestId("readiness-score")).toHaveTextContent(/0\W+\/ 100/);
    const next = screen.getByTestId("training-next");
    expect(within(next).getByText("Answers to doubts")).toBeInTheDocument();
    expect(within(next).getByText("A sales method")).toBeInTheDocument();
  });

  it("does not ask for what belongs to each product: no documents, no testimonials", async () => {
    stub();
    show();
    await screen.findByTestId("readiness-score");
    const sections = screen.getAllByRole("tab").filter((t) => !t.hasAttribute("data-mode"));
    expect(sections.map((t) => t.textContent)).toEqual(["Overview", "My way of selling", "My answers", "Fix a message"]);
    expect(screen.queryByText(/Proof and material/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Material on your products/i)).not.toBeInTheDocument();
  });

  it("goes to the right section from a thing to do", async () => {
    stub();
    const user = userEvent.setup();
    show();
    const next = await screen.findByTestId("training-next");
    await user.click(within(next).getAllByRole("button", { name: "Complete" })[0]!);
    expect(await screen.findByTestId("training-objections")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "My answers" })).toHaveAttribute("aria-selected", "true");
  });

  it("teaches an answer to a doubt from a starter, and saves it with the method", async () => {
    const { saves } = stub();
    const user = userEvent.setup();
    show();
    await user.click(await tab("My answers"));
    await user.click(await screen.findByRole("button", { name: "It is too expensive" }));
    const objections = screen.getByTestId("training-objections");
    expect(within(objections).getByLabelText("When the prospect says…")).toHaveValue("It is too expensive");
    await user.type(within(objections).getByLabelText("You answer…"), "I understand. What worries you most?");
    await user.selectOptions(within(objections).getByLabelText("About"), "alpha");

    await user.click(await tab("My way of selling"));
    await user.click(screen.getByRole("radio", { name: "Expert who teaches" }));
    expect(screen.getByTestId("approach-text")).toHaveTextContent("insight");

    await user.click(screen.getByRole("button", { name: /Save the training/ }));
    await waitFor(() => expect(saves).toHaveLength(1));
    const communication = saves[0]!.settings.commercial.communication;
    expect(communication.training).toEqual([{ kind: "objection", when: "It is too expensive", text: "I understand. What worries you most?", product: "alpha" }]);
    expect(communication.playbook).toMatchObject({ approach: "challenger", closing: "choice", discounts: "never_offer", competitors: "stay_positive" });
    expect(communication.languages).toEqual({ default: "en", supported: ["en"] });
    expect(await screen.findByText("Training saved")).toBeInTheDocument();
  });

  it("lets the client decide what happens when a customer asks for a discount or names a competitor", async () => {
    const { saves } = stub();
    const user = userEvent.setup();
    show();
    await user.click(await tab("My way of selling"));
    await user.click(screen.getByRole("radio", { name: "A person takes over" }));
    await user.click(screen.getByRole("radio", { name: "Does not talk about it" }));
    await user.click(screen.getByRole("button", { name: /Save the training/ }));
    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0]!.settings.commercial.communication.playbook).toMatchObject({ discounts: "ask_a_person", competitors: "avoid" });
  });

  it("warns about a lesson that holds a price and does not save it", async () => {
    const { saves } = stub();
    const user = userEvent.setup();
    show();
    await user.click(await tab("My answers"));
    await user.click(screen.getByRole("button", { name: "Add a question" }));
    const faq = screen.getByTestId("training-faq");
    await user.type(within(faq).getByLabelText("The question"), "How much?");
    await user.type(within(faq).getByLabelText("Your answer"), "It costs 49 EUR");
    expect(screen.getAllByText(/cannot be written here/).length).toBeGreaterThan(0);
    // Nothing that can be saved: the lesson is left out, so there is nothing to send.
    expect(screen.getByRole("button", { name: /Save the training/ })).toBeDisabled();
    expect(saves).toEqual([]);
  });

  it("tells a half-written lesson from a complete one", async () => {
    stub();
    const user = userEvent.setup();
    show();
    await user.click(await tab("My answers"));
    await user.click(screen.getByRole("button", { name: "Add an example" }));
    await user.type(within(screen.getByTestId("training-examples")).getByLabelText("When the prospect…"), "asks who you are");
    expect(screen.getByText(/\W1\W+lesson\(s\) are incomplete/)).toBeInTheDocument();
  });

  it("shows what was taught before, with the method it chose", async () => {
    stub({
      commercial: {
        ...COMMERCIAL,
        communication: { languages: { default: "en", supported: ["en"] }, playbook: { approach: "direct", closing: "soft", urgency: "none", discounts: "ask_a_person" }, training: [{ kind: "never", text: "Promise a result" }, { kind: "faq", when: "Refund?", text: "Yes.", product: "alpha" }] },
      },
    });
    const user = userEvent.setup();
    show();
    await user.click(await tab("My way of selling"));
    expect(screen.getByRole("radio", { name: "Direct and quick" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "A person takes over" })).toBeChecked();
    expect(within(screen.getByTestId("training-never")).getByDisplayValue("Promise a result")).toBeInTheDocument();
    await user.click(await tab("My answers"));
    expect(within(screen.getByTestId("training-faq")).getByLabelText("About")).toHaveValue("alpha");
    expect(screen.getByRole("button", { name: /Save the training/ })).toBeDisabled();
  });

  it("lets the client try a sentence, then try it again after saving to compare", async () => {
    const { said } = stub({ replies: ["Price answer v1", "Price answer v2"] });
    const user = userEvent.setup();
    show();
    await user.click(await tab("Fix a message"));
    const card = await screen.findByTestId("training-try");
    const go = within(card).getByRole("button", { name: "See the answer" });
    expect(go).toBeDisabled();
    await user.type(within(card).getByLabelText("The prospect says…"), "Too expensive");
    await user.click(go);
    expect(await within(card).findByText("Price answer v1")).toBeInTheDocument();
    expect(within(card).queryByTestId("try-before")).not.toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "See the answer" }));
    expect(await within(card).findByText("Price answer v2")).toBeInTheDocument();
    expect(within(within(card).getByTestId("try-before")).getByText("Price answer v1")).toBeInTheDocument();
    expect(said).toEqual(["Too expensive", "Too expensive"]);
  });

  it("says plainly when the agent would not answer", async () => {
    stub({ replies: [null] });
    const user = userEvent.setup();
    show();
    await user.click(await tab("Fix a message"));
    const card = await screen.findByTestId("training-try");
    await user.type(within(card).getByLabelText("The prospect says…"), "Stop writing to me");
    await user.click(within(card).getByRole("button", { name: "See the answer" }));
    expect(await within(card).findByText(/would not answer here/)).toBeInTheDocument();
  });
});
