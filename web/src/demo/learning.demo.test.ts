/** The Demo account shows what the agent "learned" and lets the client decide, in memory only. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clientLearning, clientLearningChoice } from "../client/api";
import { resetDemo } from "./demoApi";
import { setDemo } from "./demoMode";

let network: ReturnType<typeof vi.fn>;

beforeEach(() => {
  network = vi.fn(() => Promise.reject(new Error("the network must not be used in demo mode")));
  vi.stubGlobal("fetch", network);
  resetDemo();
  setDemo(true);
});

afterEach(() => {
  setDemo(false);
  vi.unstubAllGlobals();
});

describe("demo: what the agent learned", () => {
  it("tells a story with points to follow, to avoid and to watch", async () => {
    const answer = await clientLearning();
    expect(answer.counts.studied).toBeGreaterThanOrEqual(answer.counts.needed);
    const states = new Set(answer.notes.map((n) => n.state));
    expect(states).toEqual(new Set(["active", "watching"]));
    expect(answer.notes.some((n) => n.kind === "avoid" && n.state === "active")).toBe(true);
  });

  it("changes only the in-memory state, and reset restores it", async () => {
    await clientLearningChoice("short_messages", "off");
    expect((await clientLearning()).notes.find((n) => n.id === "short_messages")?.state).toBe("off");
    await clientLearningChoice("ask_before_proposing", "confirm");
    expect((await clientLearning()).notes.find((n) => n.id === "ask_before_proposing")).toMatchObject({ state: "active", confirmed: true });
    resetDemo();
    expect((await clientLearning()).notes.find((n) => n.id === "short_messages")?.state).toBe("active");
    expect(network).not.toHaveBeenCalled();
  });

  it("refuses a point it does not know", async () => {
    await expect(clientLearningChoice("nothing", "off")).rejects.toMatchObject({ status: 404 });
  });
});
