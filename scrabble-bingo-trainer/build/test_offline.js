// Verify the service worker makes the app fully usable with the network cut.
const { chromium } = require("playwright");
(async () => {
  const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  let fail = 0;
  const ok = (c, m) => { if (!c) { console.error("FAIL:", m); fail++; } else console.log("ok:", m); };

  await page.goto("http://localhost:8137/index.html");
  await page.waitForFunction(() => navigator.serviceWorker && navigator.serviceWorker.controller, { timeout: 8000 })
    .catch(() => {});
  // give the SW a moment to finish caching all assets
  await page.waitForTimeout(1500);
  const controlled = await page.evaluate(() => !!(navigator.serviceWorker && navigator.serviceWorker.controller));
  ok(controlled, "service worker is controlling the page");

  // cut the network entirely, then reload
  await ctx.setOffline(true);
  await page.reload();
  const rendered = await page.waitForFunction(
    () => document.querySelectorAll("#board .tile").length === 7, { timeout: 8000 }
  ).then(() => true).catch(() => false);
  ok(rendered, "app renders a full rack while OFFLINE (subway test)");

  // and it's still interactive offline
  await page.click("#btnShuffle");
  await page.waitForTimeout(100);
  ok((await page.$$("#board .tile")).length === 7, "shuffle works offline");

  await browser.close();
  console.log(fail ? `\n${fail} OFFLINE CHECK(S) FAILED` : "\nALL OFFLINE CHECKS PASSED");
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
