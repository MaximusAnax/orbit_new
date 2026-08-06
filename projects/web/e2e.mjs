/**
 * End-to-end verification: drive each project's primary user story against the
 * real gateway in a real browser.
 *
 * This is the frontend translation of the projects' eval discipline — a screen
 * that renders but doesn't work fails here, and console errors fail the run
 * rather than being ignored.
 *
 *   node e2e.mjs                 # all registered projects
 *   node e2e.mjs datasweep       # one
 *   SHOTS=1 node e2e.mjs         # also write screenshots to /tmp/shots
 */

import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE ?? "http://127.0.0.1:8077";
const SHOTS = process.env.SHOTS === "1";
const SHOT_DIR = "/tmp/shots";
const DEMO_CSV = "/home/user/orbit_new/projects/web/.demo-data/demo_sales.csv";

if (SHOTS) mkdirSync(SHOT_DIR, { recursive: true });

/** Each story: navigate, act, and assert something real rendered. */
const STORIES = {
  ethos: async (p, shot) => {
    await p.goto(`${BASE}/#/ethos`, { waitUntil: "networkidle" });
    await shot("ethos-ask");
    await p.click(".suggestion");
    await p.waitForSelector(".persp", { timeout: 30000 });
    const traditions = await p.$$eval(".persp", (n) => n.length);
    const quotes = await p.$$eval(".quote", (n) => n.length);
    await shot("ethos-answer");
    return { traditions, quotes, ok: traditions > 0 && quotes > 0 };
  },

  datasweep: async (p, shot) => {
    await p.goto(`${BASE}/#/datasweep`, { waitUntil: "networkidle" });
    await shot("datasweep-upload");
    await p.setInputFiles("input[type=file]", DEMO_CSV);
    await p.waitForSelector(".run-view", { timeout: 60000 });
    const issues = await p.$$eval(".issue", (n) => n.length);
    const found = await p.$$eval(".found-value", (n) => n.length);
    await shot("datasweep-result");

    await p.click("button[role=tab]:nth-child(2)");
    await p.waitForTimeout(400);
    await shot("datasweep-review");
    const decisions = await p.$$eval(".review-actions button", (n) => n.length);
    let decided = false;
    if (decisions > 0) {
      await p.click(".review-actions button");
      await p.waitForSelector(".review-item.decided", { timeout: 15000 });
      decided = true;
      await shot("datasweep-decided");
    }

    await p.click("button[role=tab]:nth-child(3)");
    await p.waitForSelector(".table tbody tr", { timeout: 10000 });
    const cols = await p.$$eval(".table tbody tr", (n) => n.length);
    await shot("datasweep-columns");
    return { issues, found, decisions, decided, cols, ok: issues > 0 && found > 0 && cols > 0 };
  },

  chessmentor: async (p, shot) => {
    await p.goto(`${BASE}/#/chessmentor`, { waitUntil: "networkidle" });
    await p.waitForSelector(".board", { timeout: 30000 });
    await shot("chess-board");
    const squares = await p.$$eval(".square", (n) => n.length);
    const pieces = await p.$$eval(".piece", (n) => n.length);
    // Play 1.e4 by clicking the origin then the destination.
    await p.click('[data-square="e2"]');
    await p.waitForTimeout(200);
    await p.click('[data-square="e4"]');
    await p.waitForSelector(".move-list .move", { timeout: 30000 });
    await p.waitForTimeout(600);
    const moves = await p.$$eval(".move-list .move", (n) => n.length);
    await shot("chess-played");
    return { squares, pieces, moves, ok: squares === 64 && pieces === 32 && moves >= 1 };
  },

  almanac: async (p, shot) => {
    await p.goto(`${BASE}/#/almanac`, { waitUntil: "networkidle" });
    await p.waitForSelector(".quote-card, .state-empty", { timeout: 20000 });
    const cards = await p.$$eval(".quote-card", (n) => n.length);
    await shot("almanac-today");
    return { cards, ok: cards > 0 };
  },

  flowlist: async (p, shot) => {
    await p.goto(`${BASE}/#/flowlist`, { waitUntil: "networkidle" });
    await p.waitForSelector(".table tbody tr, .state-empty", { timeout: 20000 });
    const rows = await p.$$eval(".table tbody tr", (n) => n.length);
    await shot("flowlist-list");
    return { rows, ok: rows > 0 };
  },

  dresscast: async (p, shot) => {
    await p.goto(`${BASE}/#/dresscast`, { waitUntil: "networkidle" });
    await p.waitForSelector(".outfit, .state-empty", { timeout: 25000 });
    const items = await p.$$eval(".outfit-item", (n) => n.length);
    await shot("dresscast-brief");
    return { items, ok: items > 0 };
  },

  pointsmax: async (p, shot) => {
    await p.goto(`${BASE}/#/pointsmax`, { waitUntil: "networkidle" });
    await p.waitForSelector(".stat-row, .state-empty", { timeout: 20000 });
    const stats = await p.$$eval(".stat", (n) => n.length);
    await shot("pointsmax-wallet");
    return { stats, ok: stats > 0 };
  },

  newsalpha: async (p, shot) => {
    await p.goto(`${BASE}/#/newsalpha`, { waitUntil: "networkidle" });
    await p.waitForSelector(".table tbody tr, .state-empty", { timeout: 25000 });
    const rows = await p.$$eval(".table tbody tr", (n) => n.length);
    await shot("newsalpha-signals");
    return { rows, ok: rows > 0 };
  },

  tickerpress: async (p, shot) => {
    await p.goto(`${BASE}/#/tickerpress`, { waitUntil: "networkidle" });
    await p.waitForSelector(".table tbody tr, .story, .state-empty", { timeout: 25000 });
    const rows = await p.$$eval(".table tbody tr, .story", (n) => n.length);
    await shot("tickerpress-digest");
    return { rows, ok: rows > 0 };
  },

  grailtrader: async (p, shot) => {
    await p.goto(`${BASE}/#/grailtrader`, { waitUntil: "networkidle" });
    await p.waitForSelector(".stat-row, .table tbody tr, .state-empty", { timeout: 30000 });
    const stats = await p.$$eval(".stat", (n) => n.length);
    await shot("grailtrader-portfolio");
    return { stats, ok: stats > 0 };
  },

  formcoach: async (p, shot) => {
    await p.goto(`${BASE}/#/formcoach`, { waitUntil: "networkidle" });
    await p.waitForSelector(".session, .table tbody tr, .state-empty", { timeout: 25000 });
    const rows = await p.$$eval(".session, .table tbody tr", (n) => n.length);
    await shot("formcoach-program");
    return { rows, ok: rows > 0 };
  },

  voicekin: async (p, shot) => {
    await p.goto(`${BASE}/#/voicekin`, { waitUntil: "networkidle" });
    await p.waitForSelector(".panel", { timeout: 20000 });
    const panels = await p.$$eval(".panel", (n) => n.length);
    await shot("voicekin-consent");
    return { panels, ok: panels > 0 };
  },
};

const wanted = process.argv.slice(2);
const names = wanted.length ? wanted : Object.keys(STORIES);

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const results = [];

for (const name of names) {
  const story = STORIES[name];
  if (!story) {
    results.push({ name, ok: false, note: "no story defined" });
    continue;
  }
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  const errors = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text().slice(0, 200));
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`.slice(0, 200)));

  const shot = async (label) => {
    if (SHOTS) await page.screenshot({ path: `${SHOT_DIR}/${label}.png`, fullPage: true });
  };

  try {
    const out = await story(page, shot);
    // A console error means the screen is lying about working.
    results.push({ name, ...out, errors: errors.length, ok: out.ok && errors.length === 0,
                   errorText: errors.slice(0, 2) });
  } catch (err) {
    results.push({ name, ok: false, note: String(err.message).split("\n")[0].slice(0, 120),
                   errors: errors.length, errorText: errors.slice(0, 2) });
  }
  await page.close();
}

await browser.close();

let failed = 0;
for (const r of results) {
  const { name, ok, note, errorText, ...rest } = r;
  if (!ok) failed++;
  const detail = Object.entries(rest)
    .filter(([k]) => k !== "errors")
    .map(([k, v]) => `${k}=${v}`)
    .join(" ");
  console.log(`${ok ? "PASS" : "FAIL"}  ${name.padEnd(12)} ${detail}${note ? `  (${note})` : ""}`);
  if (errorText?.length) errorText.forEach((e) => console.log(`        console: ${e}`));
}
console.log(`\n${results.length - failed}/${results.length} stories passed`);
process.exit(failed ? 1 : 0);
