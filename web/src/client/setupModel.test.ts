import { describe, expect, it } from "vitest";
import type { ChannelState, Readiness, SourceState } from "./api";
import { buildSteps, platformReasons, progressOf } from "./setupModel";
import type { Doc } from "./settingsModel";

const commercial: Doc = {
  business: { name: "Acme", summary: "Nous vendons" },
  communication: { languages: { default: "fr", supported: ["fr"] } },
  catalog: { products: [{ id: "p", status: "active", name: "P", summary: "S" }, { id: "q", status: "inactive", name: "Q", summary: "S" }] },
  payment: { methods: [{ id: "m", status: "active" }, { id: "n", status: "inactive" }] },
};

const ready: Readiness = { ready: true, checks: [{ id: "source_connected", ok: true, message: null }, { id: "channel_ready", ok: true, message: null }] };
const channel = (status: ChannelState["status"]) => ({ status }) as ChannelState;
const source = (access: SourceState["access"]) => ({ access }) as SourceState;

describe("setup steps", () => {
  it("is all done when the business, a product, the source and the channel are in place", () => {
    const steps = buildSteps({ commercial, readiness: ready, channel: channel("ready"), source: source(null) });
    expect(steps.map((s) => [s.id, s.state])).toEqual([["business", "done"], ["products", "done"], ["payments", "done"], ["prospects", "done"], ["channel", "done"]]);
    expect(progressOf(steps)).toEqual({ done: 4, total: 4 });
  });

  it("counts only active, complete products and active payment methods", () => {
    const steps = buildSteps({ commercial, readiness: ready, channel: null, source: null });
    expect(steps.find((s) => s.id === "products")!.value).toBe(1);
    expect(steps.find((s) => s.id === "payments")!.value).toBe(1);
  });

  it("starts empty for a client that has set nothing up", () => {
    const steps = buildSteps({ commercial: undefined, readiness: null, channel: null, source: null });
    expect(steps.every((s) => s.state === "todo")).toBe(true);
    expect(progressOf(steps)).toEqual({ done: 0, total: 4 });
  });

  it("does not make payments a required step", () => {
    const steps = buildSteps({ commercial: undefined, readiness: null, channel: null, source: null });
    expect(steps.find((s) => s.id === "payments")!.required).toBe(false);
  });

  it("says 'waiting' (not a to-do) when Revansa has to connect the channel or the CRM", () => {
    const readiness: Readiness = { ready: false, checks: [{ id: "source_connected", ok: false, message: "x" }, { id: "channel_ready", ok: false, message: "y" }] };
    const steps = buildSteps({ commercial, readiness, channel: channel("waiting_platform"), source: source("waiting_platform") });
    expect(steps.find((s) => s.id === "prospects")!.state).toBe("waiting");
    expect(steps.find((s) => s.id === "channel")!.state).toBe("waiting");
  });

  it("keeps a failing source or channel a to-do when the client can fix it", () => {
    const readiness: Readiness = { ready: false, checks: [{ id: "source_connected", ok: false, message: "x" }, { id: "whatsapp_number", ok: false, message: "y" }] };
    const steps = buildSteps({ commercial, readiness, channel: channel("action_required"), source: source("ready") });
    expect(steps.find((s) => s.id === "prospects")!.state).toBe("todo");
    expect(steps.find((s) => s.id === "channel")!.state).toBe("todo");
  });
});

describe("what Revansa has to do", () => {
  it("lists only the platform's failing checks", () => {
    const readiness: Readiness = {
      ready: false,
      checks: [
        { id: "enabled_by_administrator", ok: false, message: "off" },
        { id: "loaded_by_platform", ok: true, message: null },
        { id: "model_assigned", ok: false, message: "no model" },
        { id: "source_connected", ok: false, message: "mine" },
        { id: "settings_valid", ok: false, message: "mine too" },
      ],
    };
    expect(platformReasons(readiness)).toEqual(["enabled_by_administrator", "model_assigned"]);
    expect(platformReasons(null)).toEqual([]);
  });
});
