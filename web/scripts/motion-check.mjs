import { chromium } from "playwright-core";
const b = await chromium.launch({ channel: "chrome" });
const run = async (label, media) => {
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, ...media });
  const p = await ctx.newPage();
  await p.goto("http://127.0.0.1:5173/#/setup");
  await p.getByRole("button", { name: /Skip to the live demo/ }).click();
  await p.waitForTimeout(1500);
  const r = await p.evaluate(() => {
    const anims = document.getAnimations().filter((a) => a.playState === "running");
    const inf = anims.filter((a) => a.effect?.getComputedTiming().iterations === Infinity);
    const glass = document.querySelector(".glass");
    const cs = getComputedStyle(glass);
    return { running: anims.length, infiniteLoops: inf.length, backdrop: cs.backdropFilter || cs.webkitBackdropFilter, bg: cs.backgroundImage.slice(0, 30) };
  });
  console.log(label.padEnd(24), JSON.stringify(r));
  await ctx.close();
};
await run("normal", {});
await run("reduced motion", { reducedMotion: "reduce" });
await b.close();
