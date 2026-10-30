import { describe, expect, it } from "vitest";
import { en } from "./en";
import { frClient } from "./client.fr";
import { frLegacy } from "./client.legacy.fr";
import { frLanding } from "./landing.fr";
import { arLanding } from "./landing.ar";

describe("French catalogs", () => {
  it("only translate keys that exist in the English reference", () => {
    const unknown = Object.keys({ ...frLegacy, ...frClient, ...frLanding }).filter((k) => !(k in en));
    expect(unknown).toEqual([]);
  });
  it("keep every placeholder of the English text", () => {
    const fr: Record<string, string> = { ...frLegacy, ...frClient, ...frLanding };
    const names = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
    const broken = Object.entries(fr).filter(([k, v]) => JSON.stringify(names(v)) !== JSON.stringify(names((en as Record<string, string>)[k]!)));
    expect(broken.map(([k]) => k)).toEqual([]);
  });
  it("carry every public landing key in French and Arabic, with the same placeholders", () => {
    const names = (v: string) => [...v.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
    for (const catalog of [frLanding, arLanding] as Array<Record<string, string>>) {
      const keys = Object.keys(catalog);
      expect(keys.sort()).toEqual(Object.keys(en).filter((k) => k.startsWith("lp.")).sort());
      const broken = keys.filter((k) => JSON.stringify(names(catalog[k]!)) !== JSON.stringify(names((en as Record<string, string>)[k]!)));
      expect(broken).toEqual([]);
    }
  });
});
