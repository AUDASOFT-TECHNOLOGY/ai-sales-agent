// Visual verification helper: screenshots of the prototype at several viewports (Chrome via playwright-core).
// Usage: node scripts/shots.mjs [baseUrl]   Output: web/.verify/*.png (git-ignored)
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

const base = process.argv[2] ?? "http://127.0.0.1:5173";
const out = new URL("../.verify/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
mkdirSync(out, { recursive: true });

const browser = await chromium.launch({ channel: "chrome" });
const errors = [];

async function session(name, viewport, steps, opts = {}) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 1, ...opts });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(`${name}: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`${name} console: ${m.text()}`);
  });
  await page.goto(base + "/#/");
  await page.waitForTimeout(600);
  await steps(page, (label) => page.screenshot({ path: `${out}${name}-${label}.png` }));
  await ctx.close();
}

const restart = async (page) => {
  await page.getByRole("button", { name: "Prototype controls" }).click();
  await page.getByRole("button", { name: "Restart onboarding" }).click();
  await page.waitForTimeout(900);
};

const live = async (page) => {
  await page.evaluate(() => (window.location.hash = "#/setup"));
  await page.getByRole("button", { name: /Skip to the live demo/ }).click();
  await page.waitForTimeout(1200);
};

await session("desktop", { width: 1440, height: 900 }, async (page, shot) => {
  await shot("0-home");
  await restart(page);
  await shot("1-runway");
  await live(page);
  await shot("2-command");
  await page.evaluate(() => window.scrollTo(0, 900));
  await page.waitForTimeout(500);
  await shot("2b-command-scrolled");
  await page.evaluate(() => (window.location.hash = "#/prospects"));
  await page.waitForTimeout(1000);
  await shot("3-prospects-list");
  await page.evaluate(() => (window.location.hash = "#/prospects?open=amira"));
  await page.waitForTimeout(1200);
  await shot("3a-prospects-dossier");
  await page.evaluate(() => (window.location.hash = "#/prospects?view=river"));
  await page.waitForTimeout(800);
  await shot("3b-prospects-river");
  await page.evaluate(() => (window.location.hash = "#/conversations/leila"));
  await page.waitForTimeout(1000);
  await shot("4-conversation-payment");
  await page.evaluate(() => (window.location.hash = "#/conversations/amira"));
  await page.waitForTimeout(800);
  await shot("4b-conversation-handoff");
  await page.evaluate(() => (window.location.hash = "#/configure/offers"));
  await page.waitForTimeout(900);
  await shot("5-configure-offers");
  await page.evaluate(() => (window.location.hash = "#/configure/schedule"));
  await page.waitForTimeout(800);
  await shot("5b-configure-schedule");
  await page.evaluate(() => (window.location.hash = "#/configure/autonomy"));
  await page.waitForTimeout(800);
  await shot("5c-configure-autonomy");
  await page.evaluate(() => (window.location.hash = "#/configure/strategy"));
  await page.waitForTimeout(900);
  await shot("5d-configure-strategy");
  await page.getByRole("button", { name: "How your agent chooses the language" }).click();
  await page.waitForTimeout(500);
  await page.evaluate(() => window.scrollTo(0, 700));
  await page.waitForTimeout(400);
  await shot("5e-configure-strategy-more");
});

await session("laptop", { width: 1180, height: 780 }, async (page, shot) => {
  await live(page);
  await shot("command");
  await page.evaluate(() => (window.location.hash = "#/prospects?open=amira"));
  await page.waitForTimeout(1000);
  await shot("prospects");
});

await session("tablet", { width: 820, height: 1100 }, async (page, shot) => {
  await restart(page);
  await shot("runway");
  await live(page);
  await shot("command");
  await page.evaluate(() => (window.location.hash = "#/conversations/leila"));
  await page.waitForTimeout(900);
  await shot("conversation");
});

await session("mobile", { width: 390, height: 844 }, async (page, shot) => {
  await shot("home");
  await restart(page);
  await shot("runway");
  await live(page);
  await shot("command");
  await page.evaluate(() => (window.location.hash = "#/prospects"));
  await page.waitForTimeout(900);
  await shot("prospects");
  await page.evaluate(() => (window.location.hash = "#/conversations/leila"));
  await page.waitForTimeout(900);
  await shot("conversation");
  await page.evaluate(() => (window.location.hash = "#/configure"));
  await page.waitForTimeout(900);
  await shot("configure");
}, { isMobile: true, hasTouch: true });

await session("rtl", { width: 1440, height: 900 }, async (page, shot) => {
  await page.evaluate(() => window.localStorage.setItem("revensa.locale", "ar"));
  await page.reload();
  await page.waitForTimeout(600);
  await shot("home");
  await restart(page);
  await shot("runway");
  await page.getByRole("button", { name: /Skip to the live demo/ }).click();
  await page.waitForTimeout(1400);
  await shot("command");
});

await browser.close();
console.log(errors.length ? "ERRORS:\n" + errors.join("\n") : "No page errors or console errors.");
