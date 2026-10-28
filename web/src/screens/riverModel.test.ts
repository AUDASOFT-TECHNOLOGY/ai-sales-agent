import { describe, expect, it } from "vitest";
import type { PaymentCase, ProspectRow } from "../client/api";
import { bankOf } from "./riverModel";

const base: ProspectRow = {
  id: "p", display_name: "A B", status: "active", stage: null, locale: null, lane_id: null, phone: null, email: null,
  preferred_channel: null, next_action_type: null, next_action_due_at: null, last_contacted_at: null,
  last_inbound_at: null, follow_up_count: 0,
};
const row = (patch: Partial<ProspectRow>): ProspectRow => ({ ...base, ...patch });
const pay = (progress: PaymentCase["progress"]) => ({ progress }) as PaymentCase;

describe("bankOf", () => {
  it("places a prospect who asked to stop in Stopped", () => expect(bankOf(row({ status: "opted_out" }))).toBe("stopped"));
  it("places a confirmed payment in Paid and a claimed one in Converted", () => {
    expect(bankOf(row({}), pay("paid"))).toBe("paid");
    expect(bankOf(row({}), pay("claimed"))).toBe("converted");
  });
  it("places a lost closed prospect in Stopped", () => expect(bankOf(row({ status: "closed", stage: "Perdu / non intéressé" }))).toBe("stopped"));
  it("places a prospect who cooled down or never answered in Not interested", () => {
    expect(bankOf(row({ follow_up_count: 2 }))).toBe("notInterested");
    expect(bankOf(row({ stage: "En attente de réponse" }))).toBe("notInterested");
    expect(bankOf(row({ status: "paused" }))).toBe("notInterested");
  });
  it("reads non-interested before interested", () => expect(bankOf(row({ stage: "Non intéressé", last_inbound_at: "x" }))).toBe("notInterested"));
  it("places the first contact, an exchange and an interested prospect", () => {
    expect(bankOf(row({}))).toBe("contacted");
    expect(bankOf(row({ last_inbound_at: "x" }))).toBe("discussing");
    expect(bankOf(row({ stage: "Intéressé", last_inbound_at: "x" }))).toBe("interested");
  });
});
