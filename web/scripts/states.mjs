// Renders every Pulse state side by side, and a few key end states, for visual review.
import { chromium } from "playwright-core";
const b = await chromium.launch({ channel: "chrome" });
const ctx = await b.newContext({ viewport: { width: 1280, height: 720 } });
const p = await ctx.newPage();
await p.goto("http://127.0.0.1:5173/#/setup");
await p.waitForTimeout(600);
await p.evaluate(() => {
  const states = ["idle", "working", "writing", "waiting", "observing", "off_hours", "paused", "degraded", "not_live"];
  const wrap = document.createElement("div");
  wrap.style.cssText = "position:fixed;inset:0;z-index:999;background:#070914;display:grid;grid-template-columns:repeat(5,1fr);gap:18px 0;align-content:center;justify-items:center;padding:24px";
  for (const s of states) {
    const cell = document.createElement("div");
    cell.style.cssText = "display:grid;justify-items:center;gap:26px;color:#b7bfd3;font:14px Geist Variable,sans-serif";
    cell.innerHTML = `<span class="pulse" data-state="${s}" data-tier="hero" style="--size:110px"><span class="pulse__halo"></span><span class="pulse__core"></span><span class="pulse__caustic"></span><span class="pulse__ring"></span><span class="pulse__arc"></span><span class="pulse__sat" style="--t:9s;--r:20deg"></span><span class="pulse__sat" style="--t:13s;--r:140deg"></span><span class="pulse__sat" style="--t:17s;--r:255deg"></span></span><span>${s}</span>`;
    wrap.appendChild(cell);
  }
  const mini = document.createElement("div");
  mini.style.cssText = "display:flex;gap:18px;align-items:center;color:#b7bfd3;font:14px Geist Variable,sans-serif";
  mini.innerHTML = ["idle", "working", "waiting", "degraded"].map((s) => `<span class="pulse" data-state="${s}" data-tier="mini" style="--size:24px"><span class="pulse__halo"></span><span class="pulse__core"></span><span class="pulse__ring"></span><span class="pulse__arc"></span></span>`).join("") + " mini";
  wrap.appendChild(mini);
  document.body.appendChild(wrap);
});
await p.waitForTimeout(1500);
await p.screenshot({ path: new URL("../.verify/pulse-states.png", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1") });
await b.close();
