// Recomputes WCAG contrast for the documented token pairs (docs/naga-client-ui-design-system.md, section 2.3),
// reading the real values from src/styles/tokens.css. Fails the build below the stated thresholds.
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../src/styles/tokens.css", import.meta.url), "utf8");
const tokens = {};
for (const m of css.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) tokens[m[1]] = m[2];
const hex = (name) => {
  const v = tokens[name];
  if (!v) throw new Error(`token --${name} not found in tokens.css`);
  return v;
};
const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const lum = (c) => {
  const f = (v) => ((v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
  const [r, g, b] = c.map(f);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const ratio = (a, b) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
const over = (fg, a, bg) => fg.map((v, i) => Math.round(v * a + bg[i] * (1 - a)));

const page = rgb(hex("abyss-0"));
const white = [255, 255, 255];
const aurora = over(rgb(hex("aether-500")), 0.18, page); // brightest point of the aurora
const surfaces = {
  page,
  "glass 1": over(white, 0.045, page),
  "glass 2": over(white, 0.07, page),
  "glass 2 on aurora (worst case)": over(white, 0.07, aurora),
  well: over([0, 0, 0], 0.3, page),
};

const failures = [];
const check = (label, fg, bg, min) => {
  const r = ratio(fg, bg);
  const ok = r >= min;
  if (!ok) failures.push(`${label}: ${r.toFixed(2)} < ${min}`);
  return `${ok ? "PASS" : "FAIL"} ${r.toFixed(2).padStart(6)} (min ${min})  ${label}`;
};

const lines = [];
const texts = [
  ["text-1", hex("text-1"), 7],
  ["text-2", hex("text-2"), 7],
  ["text-3", hex("text-3"), 4.5],
  ["aether-400", hex("aether-400"), 4.5],
  ["ice-400", hex("ice-400"), 4.5],
  ["emerald-400", hex("emerald-400"), 4.5],
  ["amber-400", hex("amber-400"), 4.5],
  ["rose-400", hex("rose-400"), 4.5],
  ["champagne-400", hex("champagne-400"), 4.5],
];
for (const [name, value, min] of texts)
  for (const [surface, bg] of Object.entries(surfaces)) lines.push(check(`${name} on ${surface}`, rgb(value), bg, min));

// Filled controls: text on fill
lines.push(check("primary button: text on ice-500", rgb("#04121a"), rgb(hex("ice-500")), 4.5));
lines.push(check("success button: text on emerald-500", rgb("#031a12"), rgb(hex("emerald-500")), 4.5));
lines.push(check("danger button: text on rose-500", rgb("#1e0710"), rgb(hex("rose-500")), 4.5));
lines.push(check("badge: text on amber-500", rgb("#1f1403"), rgb(hex("amber-500")), 4.5));
lines.push(check("agent tint chip text aether-200 on glass 2", rgb(hex("aether-200")), surfaces["glass 2"], 4.5));

// Non-text (1.4.11): control border and focus ring, 3:1
lines.push(check("control border (white .38) on well", over(white, 0.38, surfaces.well), surfaces.well, 3));
lines.push(check("focus ring ice-300 on page", rgb(hex("ice-300")), page, 3));

console.log(lines.join("\n"));
if (failures.length) {
  console.error(`\n${failures.length} contrast failure(s):\n` + failures.join("\n"));
  process.exit(1);
}
console.log(`\nAll ${lines.length} contrast checks pass.`);
