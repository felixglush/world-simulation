// Loads a run log in headless Chromium, scrubs, clicks a farmer, and fails on any page error.
// Usage: NODE_PATH=$(npm root -g) node viewer/smoke.js RUN.jsonl [OUT_PREFIX]
const { chromium } = require("playwright");
const http = require("http");
const fs = require("fs");
const path = require("path");

const [log, out] = process.argv.slice(2);
const root = __dirname;
const types = { ".html": "text/html", ".json": "application/json" };
const server = http
  .createServer((req, res) => {
    const name = req.url.split("?")[0];
    const file = name === "/log.jsonl" ? log : path.join(root, name === "/" ? "index.html" : name);
    fs.readFile(file, (error, data) => {
      res.writeHead(error ? 404 : 200, { "content-type": types[path.extname(file)] || "text/plain" });
      res.end(error ? "" : data);
    });
  })
  .listen(0, async () => {
    const browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    const errors = [];
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.goto(`http://localhost:${server.address().port}/?log=/log.jsonl`);
    await page.waitForFunction(() => days.length > 0);
    const total = await page.evaluate(() => frameCount());
    await page.fill("#scrub", String(Math.floor(total / 2)));
    await page.dispatchEvent("#scrub", "input");
    await page.waitForTimeout(500);
    const hit = await page.evaluate(() => hits[0]);
    const box = await page.locator("#c").boundingBox();
    const scale = await page.evaluate(() => origin().scale);
    await page.mouse.click(box.x + hit.x * scale, box.y + hit.y * scale);
    await page.waitForTimeout(300);
    if (out) await page.screenshot({ path: `${out}.png` });
    const selected = await page.evaluate(() => selected);
    await browser.close();
    server.close();
    if (errors.length || !selected) {
      console.error(JSON.stringify({ errors, selected }));
      process.exit(1);
    }
    console.log(`ok: ${total} hourly frames, selected ${selected}`);
  });
