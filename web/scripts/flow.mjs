// Walks the onboarding to completion and captures the end states (Runway ceremony, seal, mood).
import { chromium } from "playwright-core";
const out = (n) => new URL(`../.verify/${n}.png`, import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const b = await chromium.launch({ channel: "chrome" });
const p = await (await b.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
const errs = [];
p.on("pageerror", (e) => errs.push(e.message));
await p.goto("http://127.0.0.1:5173/#/setup");
await p.waitForTimeout(500);
await p.getByRole("button", { name: "Test connection" }).click();
await p.waitForTimeout(2300);
await p.screenshot({ path: out("flow-1-sheet-tested") });
await p.getByRole("button", { name: "Continue" }).click();
await p.waitForTimeout(600);
await p.getByRole("button", { name: "Continue" }).click(); // offer (prefilled)
await p.waitForTimeout(700);
await p.screenshot({ path: out("flow-2-payment") });
for (let i = 0; i < 3; i++) { await p.getByRole("button", { name: "Continue" }).click(); await p.waitForTimeout(600); }
await p.screenshot({ path: out("flow-3-golive") });
await p.getByRole("button", { name: "Request activation" }).click();
await p.waitForTimeout(600);
await p.getByRole("button", { name: /simulate the administrator/i }).click();
await p.waitForTimeout(1600);
await p.screenshot({ path: out("flow-4-live-win") });
await p.evaluate(() => (window.location.hash = "#/conversations/leila"));
await p.waitForTimeout(900);
await p.getByRole("button", { name: "Review and confirm" }).first().click();
await p.waitForTimeout(500);
await p.screenshot({ path: out("flow-5-confirm-dialog") });
await p.getByLabel("I have checked this payment").check({ force: true });
await p.getByRole("button", { name: "Confirm payment" }).click();
await p.waitForTimeout(1500);
await p.screenshot({ path: out("flow-6-seal-paid") });
console.log(errs.length ? errs.join("\n") : "flow ok, no errors");
await b.close();
