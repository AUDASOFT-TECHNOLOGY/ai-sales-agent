import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nProvider } from "../../i18n";
import { LearnedCard } from "./LearnedCard";
import type { LearnedNote, LearningAnswer } from "./learningModel";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const note = (code: string, over: Partial<LearnedNote> = {}): LearnedNote => ({
  id: code,
  code,
  kind: "do",
  state: "active",
  evidence: "medium",
  cases: 6,
  against: 0,
  product: null,
  confirmed: false,
  off_by: null,
  reason: "",
  ...over,
});

function stub(initial: LearningAnswer | "down") {
  const posts: Array<{ path: string; action: string }> = [];
  const current = initial;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^.*\/client\/v1/, "");
      if (current === "down") return json(503, { detail: "down" });
      if (path === "/learning") return json(200, current);
      const match = /^\/learning\/([^/]+)\/decision$/.exec(path);
      if (match && init?.method === "POST") {
        const action = JSON.parse(String(init.body)).action as string;
        posts.push({ path, action });
        const target = current.notes.find((n) => n.id === decodeURIComponent(match[1]!))!;
        Object.assign(target, action === "off" ? { state: "off", off_by: "client" } : action === "confirm" ? { state: "active", confirmed: true } : { state: "watching", off_by: null });
        return json(200, target);
      }
      return json(404, {});
    }),
  );
  return posts;
}

const show = () =>
  render(
    <I18nProvider>
      <LearnedCard />
    </I18nProvider>,
  );

afterEach(() => vi.unstubAllGlobals());

describe("LearnedCard", () => {
  it("shows a progress bar and one short line while it is too early", async () => {
    stub({ counts: { studied: 2, achieved: 1, unfavourable: 1, needed: 5 }, notes: [] });
    show();
    expect(await screen.findByRole("progressbar")).toHaveAttribute("aria-valuenow", "2");
    expect(screen.getByText(/3\D* more finished conversation/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("says nothing stands out once enough were studied", async () => {
    stub({ counts: { studied: 12, achieved: 5, unfavourable: 7, needed: 5 }, notes: [] });
    show();
    expect(await screen.findByText(/Nothing clear yet/)).toBeInTheDocument();
  });

  it("shows the number studied, then what works and what it avoids, with a count each", async () => {
    stub({
      counts: { studied: 14, achieved: 6, unfavourable: 8, needed: 5 },
      notes: [note("short_messages", { cases: 6 }), note("doubt_ignored", { kind: "avoid", cases: 3 })],
    });
    show();
    const works = await screen.findByTestId("learned-short_messages");
    expect(within(works).getByText("Short, easy-to-read messages")).toBeInTheDocument();
    expect(within(works).getByText("6")).toBeInTheDocument();
    expect(within(screen.getByTestId("learned-doubt_ignored")).getByText("Skipping their doubt")).toBeInTheDocument();
    expect(screen.getByText("14")).toBeInTheDocument();
    expect(screen.getByText("Works")).toBeInTheDocument();
    expect(screen.getByText("Avoids")).toBeInTheDocument();
  });

  it("stays short: no paragraph, no technical word, no raw code", async () => {
    stub({ counts: { studied: 9, achieved: 4, unfavourable: 5, needed: 5 }, notes: [note("short_messages"), note("brand_new_code")] });
    show();
    await screen.findByTestId("learned-short_messages");
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/brand_new_code|short_messages|threshold|correlation|model/i);
    expect(screen.queryByTestId("learned-brand_new_code")).toBeNull();
    expect(text.length).toBeLessThan(220);
  });

  it("sets a point aside with one icon, and puts it back", async () => {
    const posts = stub({ counts: { studied: 14, achieved: 6, unfavourable: 8, needed: 5 }, notes: [note("short_messages")] });
    show();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Set aside" }));
    await waitFor(() => expect(posts).toEqual([{ path: "/learning/short_messages/decision", action: "off" }]));
    await waitFor(() => expect(screen.getByText(/Set aside \(1\)/)).toBeInTheDocument());
    await user.click(screen.getByText(/Set aside \(1\)/));
    await user.click(await screen.findByRole("button", { name: "Put back" }));
    await waitFor(() => expect(posts.at(-1)?.action).toBe("on"));
  });

  it("confirms a point that is only being watched", async () => {
    const posts = stub({ counts: { studied: 8, achieved: 3, unfavourable: 5, needed: 5 }, notes: [note("easy_next_step", { state: "watching", cases: 3 })] });
    show();
    expect(await screen.findByText("Watching")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(posts[0]?.action).toBe("confirm"));
  });

  it("says it is unavailable when the server is down, without breaking the page", async () => {
    stub("down");
    show();
    expect(await screen.findByText(/Unavailable right now/)).toBeInTheDocument();
  });
});
