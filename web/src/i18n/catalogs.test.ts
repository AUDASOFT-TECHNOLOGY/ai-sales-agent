import { describe, expect, it } from "vitest";
import { en } from "./en";
import { frClient } from "./client.fr";
import { frLegacy } from "./client.legacy.fr";

describe("French catalogs", () => {
  it("only translate keys that exist in the English reference", () => {
    const unknown = Object.keys({ ...frLegacy, ...frClient }).filter((k) => !(k in en));
    expect(unknown).toEqual([]);
  });
  it("keep every placeholder of the English text", () => {
    const fr: Record<string, string> = { ...frLegacy, ...frClient };
    const names = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
    const broken = Object.entries(fr).filter(([k, v]) => JSON.stringify(names(v)) !== JSON.stringify(names((en as Record<string, string>)[k]!)));
    expect(broken.map(([k]) => k)).toEqual([]);
  });
});
