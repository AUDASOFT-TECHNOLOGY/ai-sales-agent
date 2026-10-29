import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../../i18n";
import { ClientAuthProvider } from "../ClientAuthContext";
import { TrainingPage } from "./TrainingPage";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const COMMERCIAL = {
  communication: { languages: { default: "en", supported: ["en"] } },
  catalog: { products: [{ id: "alpha", name: "Alpha Course" }, { id: "beta", name: "Beta Course" }] },
};
const material = (id: string, readable: number) => ({ product_id: id, total: readable, readable, readable_chars: readable * 400, testimonials: 0, by_category: {}, unread: [] });

function stub(options: { commercial?: Record<string, unknown>; material?: unknown[]; suggestions?: unknown } = {}) {
  const saves: Array<{ settings: { commercial: { communication: Record<string, unknown> } } }> = [];
  const notes: Array<Record<string, unknown>> = [];
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
      if (path === "/training/material") return json(200, { products: options.material ?? [] });
      if (path.startsWith("/training/suggestions/")) return json(200, options.suggestions ?? { suggestions: [], left_out: 0 });
      if (path === "/products/alpha/resources/note" && method === "POST") {
        notes.push(JSON.parse(String(init!.body)));
        return json(201, { id: "r1", product_id: "alpha", kind: "file", category: "testimonial", title: "Leila", url: null, filename: "Leila.txt", mime_type: null, size_bytes: 10, status: "ready", status_detail: null, text_chars: 10, created_at: "2026-10-28T10:00:00Z" });
      }
      if (/\/products\/[^/]+\/resources$/.test(path)) return json(200, { resources: [], limits: { max_file_bytes: 1000, max_per_product: 10, accepted_extensions: ["pdf"] } });
      if (path === "/auth/me") return json(200, { client_id: "naga", display_name: "Naga", whatsapp_number: "+21620000000" });
      if (path === "/agent-test") return json(200, { tests: [] });
      return json(404, {});
    }),
  );
  return { saves, notes };
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
    expect(communication.playbook).toMatchObject({ approach: "challenger", closing: "choice" });
    expect(communication.languages).toEqual({ default: "en", supported: ["en"] });
    expect(await screen.findByText("Training saved")).toBeInTheDocument();
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

  it("counts the material the agent can read in the overview, and lists each product in the material section", async () => {
    stub({ material: [material("alpha", 2), material("beta", 0)] });
    const user = userEvent.setup();
    show();
    await waitFor(() => expect(within(screen.getByTestId("dim-material")).getByText(/1\W+of\W+2/)).toBeInTheDocument());
    await user.click(await tab("Proof and material"));
    const panel = await screen.findByTestId("training-material");
    expect(within(panel).getByText(/The agent can read\W+2\W+item\(s\) here/)).toBeInTheDocument();
    await user.selectOptions(within(panel).getByLabelText("Product"), "beta");
    expect(await within(panel).findByText(/The agent can read nothing about this product yet/)).toBeInTheDocument();
  });

  it("adds a pasted testimonial to the material of the product", async () => {
    const { notes } = stub({ material: [material("alpha", 0)] });
    const user = userEvent.setup();
    show();
    await user.click(await tab("Proof and material"));
    const form = await screen.findByTestId("training-note");
    const add = within(form).getByRole("button", { name: "Add to the material" });
    expect(add).toBeDisabled();
    await user.type(within(form).getByLabelText("Title"), "Leila");
    await user.type(within(form).getByLabelText("Text"), "It changed my shop.");
    await user.click(add);
    await waitFor(() => expect(notes).toEqual([{ title: "Leila", text: "It changed my shop.", category: "testimonial" }]));
    expect(await within(form).findByText("Added. The agent can read it now.")).toBeInTheDocument();
  });

  it("offers lessons found in the documents, to be kept one by one", async () => {
    stub({
      material: [material("alpha", 1)],
      suggestions: { suggestions: [{ kind: "faq", when: "Is there a refund?", text: "Yes, within fourteen days.", source: "Brochure", attribution: null }], left_out: 2 },
    });
    const user = userEvent.setup();
    show();
    await user.click(await tab("Proof and material"));
    const go = await screen.findByRole("button", { name: "Look through my documents" });
    await waitFor(() => expect(go).toBeEnabled());
    await user.click(go);
    expect(await screen.findByText("Is there a refund?")).toBeInTheDocument();
    expect(screen.getByText(/\W2\W+passage\(s\) with a price/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add" }));
    expect(screen.getByRole("button", { name: "Added" })).toBeDisabled();
    await user.click(await tab("My answers"));
    expect(within(screen.getByTestId("training-faq")).getByLabelText("The question")).toHaveValue("Is there a refund?");
  });

  it("asks to add a product first when there is none", async () => {
    stub({ commercial: { communication: { languages: { default: "en", supported: ["en"] } } } });
    const user = userEvent.setup();
    show();
    await user.click(await tab("Proof and material"));
    expect(await screen.findByText(/Add a product first/)).toBeInTheDocument();
  });

  it("shows what was taught before, with the method it chose", async () => {
    stub({
      commercial: {
        ...COMMERCIAL,
        communication: { languages: { default: "en", supported: ["en"] }, playbook: { approach: "direct", closing: "soft", urgency: "none", social_proof: "never" }, training: [{ kind: "never", text: "Promise a result" }, { kind: "faq", when: "Refund?", text: "Yes.", product: "alpha" }] },
      },
    });
    const user = userEvent.setup();
    show();
    await user.click(await tab("My way of selling"));
    expect(screen.getByRole("radio", { name: "Direct and quick" })).toBeChecked();
    expect(within(screen.getByTestId("training-never")).getByDisplayValue("Promise a result")).toBeInTheDocument();
    await user.click(await tab("My answers"));
    expect(within(screen.getByTestId("training-faq")).getByLabelText("About")).toHaveValue("alpha");
    expect(screen.getByRole("button", { name: /Save the training/ })).toBeDisabled();
  });
});
