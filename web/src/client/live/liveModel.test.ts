import { describe, expect, it } from "vitest";
import { activePhase, activeFor, checklist, clock, followedProspect, groupFeed, idleSeconds, initials, isWriting, lastPhase, nameOf, phaseOf, primaryStep, runFor, secondsOf, stepKey, type LiveAnswer, type LiveStep } from "./liveModel";

const step = (over: Partial<LiveStep>): LiveStep => ({
  id: 1,
  kind: "thinking",
  state: "running",
  started_at: "2026-01-05T09:00:00Z",
  ended_at: null,
  prospect_id: "p1",
  name: "Ana",
  detail: null,
  ...over,
});

const answer = (now: LiveStep[], recent: LiveStep[] = now): LiveAnswer => ({ at: "2026-01-05T09:00:10Z", now, recent });

describe("liveModel", () => {
  it("gives a plain sentence to a step it does not know", () => {
    expect(stepKey("thinking")).toBe("rv.live.k.thinking");
    expect(stepKey("something_new")).toBe("rv.live.k.other");
  });

  it("follows the person the client picked, else the one being worked on, else the last one", () => {
    const working = step({ id: 2, prospect_id: "p2", name: "Bo" });
    const past = step({ id: 1, state: "done", ended_at: "2026-01-05T09:00:05Z" });
    expect(followedProspect(answer([working], [working, past]), "p1")).toBe("p1");
    expect(followedProspect(answer([working], [working, past]), null)).toBe("p2");
    expect(followedProspect(answer([], [past]), null)).toBe("p1");
    expect(followedProspect(answer([], [step({ prospect_id: null, kind: "planning" })]), null)).toBeNull();
    expect(followedProspect(null, null)).toBeNull();
  });

  it("finds what the agent does for a person, and whether it is writing", () => {
    const working = step({ kind: "answering" });
    expect(activeFor(answer([working]), "p1")).toBe(working);
    expect(activeFor(answer([working]), "p2")).toBeNull();
    expect(isWriting(working)).toBe(true);
    expect(isWriting(step({ kind: "sending" }))).toBe(false);
    expect(isWriting(null)).toBe(false);
  });

  it("finds a name from any recent step of that person", () => {
    expect(nameOf(answer([], [step({ state: "done" })]), "p1")).toBe("Ana");
    expect(nameOf(answer([]), "p9")).toBeNull();
  });

  it("counts a running step up to the server's clock and a finished one to its end", () => {
    const start = Date.parse("2026-01-05T09:00:00Z");
    expect(secondsOf(step({}), start + 7400)).toBe(7);
    expect(secondsOf(step({ state: "done", ended_at: "2026-01-05T09:00:03Z" }), start + 60000)).toBe(3);
    expect(secondsOf(step({}), start - 5000)).toBe(0);
  });

  it("writes a short timer", () => {
    expect(clock(4)).toBe("4 s");
    expect(clock(65)).toBe("1 min 05 s");
    expect(clock(3600 + 120)).toBe("1 h 02 min");
  });
});

describe("the parcours and the steps for one person", () => {
  const at = (n: number) => `2026-01-05T09:00:${String(n).padStart(2, "0")}Z`;
  const run = [
    step({ id: 4, kind: "thinking", started_at: at(8) }),
    step({ id: 3, kind: "reading_history", state: "done", started_at: at(6), ended_at: at(8) }),
    step({ id: 2, kind: "checking", state: "done", started_at: at(5), ended_at: at(6) }),
    step({ id: 1, kind: "sending", state: "done", started_at: at(1), ended_at: at(3) }),
  ];

  it("places a step in the day and finds the phase being worked on", () => {
    expect(phaseOf("thinking")).toBe("prepare");
    expect(phaseOf("sending")).toBe("send");
    expect(phaseOf("syncing")).toBeNull();
    expect(activePhase(answer([run[0]!], run))).toBe("prepare");
    expect(activePhase(answer([], run))).toBeNull();
    expect(lastPhase(answer([], run))).toBe("prepare");
  });

  it("tells the stage about a person before a task about nobody", () => {
    const file = step({ id: 9, kind: "reading_prospects", prospect_id: null, name: null });
    expect(primaryStep(answer([file, run[0]!]))?.id).toBe(4);
    expect(primaryStep(answer([file]))?.id).toBe(9);
    expect(primaryStep(answer([]))).toBeNull();
  });

  it("keeps only the latest run of a person, oldest step first", () => {
    expect(runFor(answer([run[0]!], run), "p1").map((s) => s.id)).toEqual([2, 3, 4]);
    expect(runFor(answer([], run), "p9")).toEqual([]);
  });

  it("lists what was done, what runs and what is left", () => {
    const now = Date.parse(at(12));
    const items = checklist(runFor(answer([run[0]!], run), "p1"), now);
    expect(items.map((i) => [i.kind, i.state])).toEqual([
      ["checking", "done"],
      ["reading_history", "done"],
      ["thinking", "running"],
      ["sending", "todo"],
    ]);
    expect(items[2]!.seconds).toBe(4);
    const reply = checklist([step({ id: 5, kind: "answering" })], now);
    expect(reply[2]!.kind).toBe("answering");
  });

  it("groups consecutive steps of the same person, newest first", () => {
    const planning = step({ id: 7, kind: "planning", state: "done", prospect_id: null, name: null });
    const groups = groupFeed([...run, planning]);
    expect(groups).toHaveLength(2);
    expect(groups[0]!.steps.map((s) => s.id)).toEqual([1, 2, 3, 4]);
    expect(groups[0]!.state).toBe("running");
    expect(groups[1]!.prospectId).toBeNull();
    expect(groupFeed([step({ state: "failed" })])[0]!.state).toBe("failed");
  });

  it("measures the quiet and writes initials", () => {
    expect(idleSeconds(answer([], run), Date.parse(at(13)))).toBe(5);
    expect(idleSeconds(answer([], [step({})]), Date.parse(at(13)))).toBeNull();
    expect(initials("Ana Martin")).toBe("AM");
    expect(initials("Cher")).toBe("C");
    expect(initials(null)).toBe("?");
  });
});
