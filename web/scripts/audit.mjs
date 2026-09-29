// Runtime audit against the running app: accessibility (axe), interactions, real persistence,
// responsive overflow, frame rate.
// Usage: node scripts/audit.mjs [baseUrl]
import { chromium } from "playwright-core";
import AxeBuilder from "@axe-core/playwright";

const base = process.argv[2] ?? "http://127.0.0.1:5173";
const browser = await chromium.launch({ channel: "chrome" });
const problems = [];
const notes = [];

const newPage = async (viewport = { width: 1440, height: 900 }, opts = {}) => {
  const ctx = await browser.newContext({ viewport, ...opts });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text()}`));
  return page;
};
const go = async (page, hash) => {
  await page.evaluate((h) => (window.location.hash = h), hash);
  await page.waitForTimeout(1100);
};
const live = async (page) => {
  await page.goto(base + "/#/setup");
  await page.waitForTimeout(900);
  const skip = page.getByRole("button", { name: /Skip to the live demo/ });
  await skip.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await skip.click();
  await page.waitForTimeout(1200);
};

/* ── 1. Accessibility (axe) on every screen, including the new Results page and every Configure
 *  section (now eight, since Strategy split into "sales path" and "voice"). ── */
{
  const page = await newPage();
  await live(page);
  const screens = ["#/dashboard", "#/prospects", "#/prospects?open=amira", "#/prospects?view=river", "#/conversations/leila", "#/conversations/amira", "#/results", "#/history", "#/configure", "#/configure/offers"];
  for (const s of screens) {
    await go(page, s);
    const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
    const contrastIncomplete = r.incomplete.filter((i) => i.id === "color-contrast").reduce((n, i) => n + i.nodes.length, 0);
    console.log(`axe ${s}: ${r.violations.length} violations, ${contrastIncomplete} contrast checks not decidable by axe (translucent glass)`);
    for (const v of r.violations) {
      console.log(`   ${v.impact} ${v.id}: ${v.help} (${v.nodes.length}) e.g. ${v.nodes[0].target.join(" ")}`);
      problems.push(`axe ${s} ${v.id}`);
    }
  }
  for (const section of ["business", "strategy", "voice", "schedule", "whatsapp", "autonomy", "followup"]) {
    await go(page, `#/configure/${section}`);
    const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
    console.log(`axe configure/${section}: ${r.violations.length} violations`);
    for (const v of r.violations) {
      console.log(`   ${v.impact} ${v.id}: ${v.help} (${v.nodes.length}) e.g. ${v.nodes[0].target.join(" ")}`);
      problems.push(`axe configure ${section} ${v.id}`);
    }
  }
  await page.context().close();
}

/* ── 2. Interactions ── */
{
  const page = await newPage();
  await live(page);
  const count = async () => Number((await page.locator(".bar__bell-dot").textContent()) ?? 0);
  const before = await count();
  await page.locator(".need-row").filter({ hasText: "Karim" }).getByRole("button", { name: "Review" }).click();
  await page.waitForTimeout(400);
  await page.getByRole("dialog").getByRole("button", { name: "Approve and send" }).click();
  await page.waitForTimeout(700);
  const afterApprove = await count();
  console.log(`interaction: approve draft, needs ${before} -> ${afterApprove}`);
  if (afterApprove !== before - 1) problems.push("approve did not reduce the Needs You count");
  await page.locator(".need-row").filter({ hasText: "Leila" }).getByRole("button", { name: "Review" }).click();
  await page.waitForTimeout(500);
  const confirmBtn = page.getByRole("button", { name: "Confirm payment" });
  const disabledBefore = await confirmBtn.isDisabled();
  await page.getByLabel("I have checked this payment").check({ force: true });
  const disabledAfter = await confirmBtn.isDisabled();
  console.log(`interaction: confirm button disabled before attest=${disabledBefore}, after=${disabledAfter}`);
  if (!disabledBefore || disabledAfter) problems.push("payment confirm attestation gating is wrong");
  await confirmBtn.click();
  await page.waitForTimeout(700);
  console.log(`interaction: payment confirmed, needs -> ${await count().catch(() => 0)}`);
  await go(page, "#/conversations/leila");
  const noticeDone = await page.locator(".pay-notice--done").count();
  await page.getByRole("button", { name: "Details" }).click();
  await page.waitForTimeout(600);
  const whyPanel = await page.locator(".why-panel").count();
  const sealPaid = (await page.locator(".seal[data-paid]").count()) && noticeDone ? 1 : 0;
  await page.keyboard.press("Escape");
  console.log(`interaction: seal shows paid state = ${sealPaid === 1}, "why REVENSA does this" panel present = ${whyPanel === 1}`);
  if (sealPaid !== 1) problems.push("seal did not switch to the confirmed state");
  if (whyPanel !== 1) problems.push("conversation Details drawer is missing the reasoning panel");

  // Home is the Dashboard, not the onboarding
  await page.goto(base + "/#/");
  await page.waitForTimeout(800);
  const homeHash = await page.evaluate(() => window.location.hash);
  console.log(`interaction: home route -> ${homeHash}`);
  if (homeHash !== "#/dashboard") problems.push("home is not the Dashboard");

  // Autonomy dial: request the locked mode -> clamped message, using the new Prepare/Propose/Act wording
  await go(page, "#/configure/autonomy");
  await page.getByRole("radio", { name: /^Act/ }).click();
  await page.waitForTimeout(500);
  const clamp = await page.locator(".dial__clamp").textContent();
  const limitNote = await page.locator(".dial__limit-note").textContent();
  console.log(`interaction: dial clamp message = "${clamp}"; permanent limit note = "${limitNote}"`);
  if (!clamp?.includes("Propose")) problems.push("dial did not show the clamped explanation with the new mode wording");
  if (!limitNote?.toLowerCase().includes("never")) problems.push("dial is missing the permanent 'never beyond platform limits' reassurance");
  const bar = await page.locator(".publish-bar").count();
  console.log(`interaction: unpublished-changes bar visible = ${bar === 1}`);
  await page.getByRole("button", { name: "Review", exact: true }).click();
  await page.waitForTimeout(400);
  await page.getByRole("button", { name: /^Publish v8/ }).click();
  await page.waitForTimeout(500);
  console.log(`interaction: published, toast = "${(await page.locator(".toast").last().textContent())?.slice(0, 40)}"`);

  // Command palette via keyboard, including the new Results entry
  await page.keyboard.press("Control+k");
  await page.waitForTimeout(300);
  await page.keyboard.type("results");
  await page.waitForTimeout(200);
  const paletteHasResults = await page.locator(".palette__item", { hasText: "Results" }).count();
  await page.keyboard.press("Escape");
  console.log(`interaction: command palette finds Results = ${paletteHasResults > 0}`);
  if (paletteHasResults === 0) problems.push("command palette does not list the Results screen");

  await go(page, "#/dashboard");
  await page.close();
}
{
  const page = await newPage();
  await live(page);
  await page.getByRole("button", { name: "Prototype controls" }).click();
  await page.getByRole("radio", { name: "Viewer" }).click();
  await page.keyboard.press("Escape");
  await go(page, "#/dashboard");
  await page.locator(".need-row").filter({ hasText: "Leila" }).getByRole("button", { name: "Review" }).click();
  await page.waitForTimeout(500);
  const disabled = await page.getByRole("dialog").getByRole("button", { name: "Confirm payment" }).isDisabled();
  console.log(`interaction: viewer cannot confirm payments = ${disabled}`);
  if (!disabled) problems.push("viewer role can confirm payments");
  await page.context().close();
}

/* ── 3. Real persistence (section 9/10/20): every editable field is wired to the store and a
 *  reload in the same browser must show the edit, not the original seed value. This is the direct
 *  check against "no fake configuration". ── */
{
  const page = await newPage();
  await live(page);

  // Offers: rename Foundation, change its price, reload, and the OfferCard grid must show the edit.
  await go(page, "#/configure/offers");
  await page.getByLabel("Name", { exact: true }).fill("Foundation Plus");
  await page.getByLabel("Price", { exact: true }).fill("1 800 TND");
  await page.waitForTimeout(300);
  await page.reload();
  await page.waitForTimeout(1200);
  const offerCardText = await page.locator(".offer-grid").textContent();
  const offerPersisted = offerCardText?.includes("Foundation Plus") && offerCardText?.includes("1 800 TND");
  console.log(`persistence: offer edit survives reload = ${!!offerPersisted}`);
  if (!offerPersisted) problems.push("editing an offer's name/price does not persist across reload");

  // Language & style: turn English on, set it default, reload.
  await go(page, "#/configure/voice");
  await page.getByRole("button", { name: "English", exact: true }).click();
  await page.waitForTimeout(200);
  await page.getByRole("radio", { name: "English", exact: true }).click();
  await page.waitForTimeout(300);
  await page.reload();
  await page.waitForTimeout(1000);
  await go(page, "#/configure/voice");
  const englishOn = await page.getByRole("button", { name: "English", exact: true }).getAttribute("aria-pressed");
  const englishDefault = await page.getByRole("radio", { name: "English", exact: true }).getAttribute("aria-checked");
  console.log(`persistence: language/style edit survives reload = ${englishOn === "true" && englishDefault === "true"}`);
  if (englishOn !== "true" || englishDefault !== "true") problems.push("language/style edits do not persist across reload");

  // Schedule: change the daily limit via the guardrail slider, reload, and the Configure badge + Command budget must reflect it.
  await go(page, "#/configure/schedule");
  await page.locator('input[type="range"]').fill("12");
  await page.waitForTimeout(300);
  await page.reload();
  await page.waitForTimeout(1000);
  await go(page, "#/configure");
  const scheduleBadge = await page.locator(".cfg-item", { hasText: "When REVENSA works" }).textContent();
  console.log(`persistence: daily limit edit survives reload (badge reads "${scheduleBadge?.match(/\d+ a day/)?.[0]}")`);
  if (!scheduleBadge?.includes("12 a day")) problems.push("the daily limit edit does not persist across reload");

  // CEO report: change the time, reload, and the Report history page's today card must reflect it.
  await go(page, "#/configure/whatsapp");
  await page.getByLabel("Time", { exact: true }).fill("19:30");
  await page.waitForTimeout(300);
  await page.reload();
  await page.waitForTimeout(1000);
  await go(page, "#/history");
  const reportHeading = await page.locator("#report-h").textContent();
  console.log(`persistence: CEO report time edit survives reload (heading reads "${reportHeading}")`);
  if (!reportHeading?.includes("19:30")) problems.push("the CEO report time edit does not persist across reload");

  await page.context().close();
}

/* ── 4. Responsive: no horizontal page overflow, across the whole navigation including Results
 *  and the new "voice" Configure section. ── */
for (const [name, viewport, opts] of [
  ["desktop 1440", { width: 1440, height: 900 }, {}],
  ["laptop 1180", { width: 1180, height: 780 }, {}],
  ["tablet 820", { width: 820, height: 1100 }, {}],
  ["mobile 390", { width: 390, height: 844 }, { isMobile: true, hasTouch: true }],
  ["small mobile 320", { width: 320, height: 640 }, { isMobile: true, hasTouch: true }],
]) {
  const page = await newPage(viewport, opts);
  await live(page);
  const bad = [];
  for (const s of ["#/setup", "#/dashboard", "#/prospects", "#/prospects?view=river", "#/prospects?open=amira", "#/conversations/leila", "#/results", "#/history", "#/configure", "#/configure/offers", "#/configure/voice", "#/configure/schedule", "#/configure/followup", "#/configure/whatsapp", "#/configure/autonomy"]) {
    await go(page, s);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    if (overflow > 1) bad.push(`${s} (+${overflow}px)`);
  }
  console.log(`responsive ${name}: ${bad.length ? "HORIZONTAL OVERFLOW " + bad.join(", ") : "no horizontal overflow"}`);
  if (bad.length) problems.push(`overflow at ${name}: ${bad.join(", ")}`);
  await page.context().close();
}

/* ── 5. Frame rate on the heaviest screen (Dashboard: aurora + hero Pulse + charts + thread) ── */
{
  const page = await newPage();
  await live(page);
  await go(page, "#/dashboard");
  await page.waitForTimeout(1500);
  const stats = await page.evaluate(
    () =>
      new Promise((resolve) => {
        const times = [];
        let last = performance.now();
        const end = last + 4000;
        const tick = (now) => {
          times.push(now - last);
          last = now;
          if (now < end) requestAnimationFrame(tick);
          else {
            const sorted = [...times].sort((a, b) => a - b);
            const avg = times.reduce((a, b) => a + b, 0) / times.length;
            resolve({ frames: times.length, fps: Math.round(1000 / avg), p95: sorted[Math.floor(sorted.length * 0.95)].toFixed(1), slow: times.filter((t) => t > 25).length });
          }
        };
        requestAnimationFrame(tick);
      }),
  );
  console.log(`frame rate (idle, Dashboard, headless Chrome, software compositing): ${stats.fps} fps, p95 frame ${stats.p95} ms, ${stats.slow} of ${stats.frames} frames over 25 ms`);
  notes.push(stats);
  await page.context().close();
}

await browser.close();
console.log(problems.length ? `\nPROBLEMS (${problems.length}):\n- ${problems.join("\n- ")}` : "\nAudit finished with no problems.");
process.exit(problems.length ? 1 : 0);
