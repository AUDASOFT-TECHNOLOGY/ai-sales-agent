import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clientAgent,
  clientConfirmPayment,
  clientConversation,
  clientConversations,
  clientDecideProposal,
  clientLanes,
  clientPauseAgent,
  clientPayments,
  clientProposals,
  clientProspects,
  clientSaveSettings,
  clientSendMessage,
  clientSettings,
  clientSummary,
  clientTakeOver,
} from "../client/api";
import { clientDashboard } from "../client/dashboard/clientDashboardApi";
import { resetDemo } from "./demoApi";
import { isDemoCredentials, isDemoLogin, setDemo } from "./demoMode";
import { appFetch } from "./transport";

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

describe("demo access", () => {
  it("accepts only Demo / 123456789", () => {
    expect(isDemoCredentials("Demo", "123456789")).toBe(true);
    expect(isDemoCredentials(" demo ", "123456789")).toBe(true);
    expect(isDemoCredentials("Demo", "wrong")).toBe(false);
    expect(isDemoLogin("Demo")).toBe(true);
    expect(isDemoLogin("naga")).toBe(false);
  });
});

describe("demo data", () => {
  it("is rich: 4 circuits, 6 products, 32 prospects over 3 channels, 30 conversations, 11 requests", async () => {
    expect((await clientLanes()).lanes).toHaveLength(4);
    const settings = await clientSettings();
    const products = (settings.settings.commercial as { catalog: { products: unknown[] } }).catalog.products;
    expect(products.length).toBeGreaterThanOrEqual(5);
    const prospects = await clientProspects(100, 0);
    expect(prospects.total).toBe(32);
    expect(new Set(prospects.prospects.map((p) => p.preferred_channel))).toEqual(new Set(["email", "telegram", "whatsapp"]));
    expect((await clientConversations(100, 0)).total).toBe(30);
    const requests = (await clientProposals()).proposals.length + (await clientPayments()).cases.length;
    expect(requests).toBeGreaterThanOrEqual(8);
  });

  it("keeps the indicators consistent with the prospects", async () => {
    const summary = await clientSummary();
    const dashboard = await clientDashboard();
    expect(summary.total_prospects).toBe(32);
    expect(dashboard.activity.prospects_total.value).toBe(32);
    expect(dashboard.activity.conversations.value).toBe((await clientConversations(100, 0)).total);
    expect(Object.values(dashboard.activity.by_circuit).reduce((a, b) => a + b, 0)).toBe(32);
  });

  it("filters and searches prospects", async () => {
    expect((await clientProspects(100, 0, { q: "Dorra" })).total).toBe(1);
    const whatsapp = await clientProspects(100, 0, { channel: "whatsapp" });
    expect(whatsapp.prospects.every((p) => p.preferred_channel === "whatsapp")).toBe(true);
    expect((await clientProspects(100, 0, { lane: "devis-negociation" })).total).toBe(7);
  });
});

describe("demo interactions", () => {
  it("changes only the in-memory state, and reset restores it", async () => {
    expect((await clientAgent()).state).toBe("running");
    await clientPauseAgent();
    expect((await clientAgent()).state).toBe("paused");

    const before = (await clientProposals()).proposals;
    await clientDecideProposal(before[0]!.id, "reject");
    expect((await clientProposals()).proposals).toHaveLength(before.length - 1);

    const settings = await clientSettings();
    const commercial = { ...settings.settings.commercial, business: { name: "Autre" } };
    await clientSaveSettings(settings.version, { ...settings.settings, commercial });
    expect(((await clientSettings()).settings.commercial as { business: { name: string } }).business.name).toBe("Autre");

    resetDemo();
    expect((await clientAgent()).state).toBe("running");
    expect((await clientProposals()).proposals).toHaveLength(before.length);
    expect(((await clientSettings()).settings.commercial as { business: { name: string } }).business.name).toBe("Demo Studio");
  });

  it("needs the conversation to be taken over before a reply is sent, then appends it", async () => {
    const id = "demo-p22";
    await expect(clientSendMessage(id, "Bonjour", "key-0000001")).rejects.toMatchObject({ code: "not_in_human_mode" });
    await clientTakeOver(id);
    await clientSendMessage(id, "Réponse simulée", "key-0000002");
    const again = await clientSendMessage(id, "Réponse simulée", "key-0000002");
    expect(again.replayed).toBe(true);
    const history = await clientConversation(id);
    expect(history.messages.filter((m) => m.text === "Réponse simulée")).toHaveLength(1);
  });

  it("confirms a payment to check", async () => {
    const waiting = (await clientPayments()).cases.find((c) => c.progress !== "paid")!;
    const done = await clientConfirmPayment(waiting.prospect_id, { amount: "100", currency: "TND", reference: "REF-1" });
    expect(done.progress).toBe("paid");
  });
});

describe("no network", () => {
  it("never calls fetch, even for an unknown route", async () => {
    await clientAgent();
    await clientProspects(10, 0);
    const unknown = await appFetch("/client/v1/does-not-exist");
    expect(unknown.status).toBe(404);
    const outside = await appFetch("https://example.com/anything");
    expect(outside.status).toBe(404);
    expect(network).not.toHaveBeenCalled();
  });

  it("uses the real fetch again outside demo mode", async () => {
    setDemo(false);
    network.mockResolvedValueOnce(new Response("{}", { status: 200 }));
    await appFetch("/client/v1/agent");
    expect(network).toHaveBeenCalledTimes(1);
  });
});
