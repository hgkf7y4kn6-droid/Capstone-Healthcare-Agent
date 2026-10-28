// Keeps index.css in sync with src/tokens.ts and enforces the contrast
// minimums from docs/design-system.md in both themes. No deps: node --test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const ts = readFileSync(new URL("../src/tokens.ts", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/index.css", import.meta.url), "utf8");
const kebab = (k) => k.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase());

function block(name) {
  const start = ts.indexOf(`${name}: {`);
  const body = ts.slice(start, ts.indexOf("}", start));
  return Object.fromEntries([...body.matchAll(/(\w+): "(#[0-9a-f]{6})"/g)].map((m) => [m[1], m[2]]));
}
const themes = { light: block("light"), dark: block("dark") };

function lum(hex) {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
const ratio = (a, b) => {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};

test("css variables mirror tokens.ts", () => {
  const rootLight = css.slice(css.indexOf(":root {"), css.indexOf("}", css.indexOf(":root {")));
  const rootDark = css.slice(css.indexOf(':root[data-theme="dark"]'));
  for (const [k, v] of Object.entries(themes.light)) assert.match(rootLight, new RegExp(`--${kebab(k)}: ${v};`), k);
  for (const [k, v] of Object.entries(themes.dark)) assert.match(rootDark, new RegExp(`--${kebab(k)}: ${v};`), k);
});

for (const [name, t] of Object.entries(themes)) {
  test(`${name}: contrast minimums`, () => {
    const checks = [
      ["ink on surface", t.ink, t.surface, 7], ["ink on bg", t.ink, t.bg, 7],
      ["ink-soft on surface", t.inkSoft, t.surface, 4.5], ["ink-soft on bg", t.inkSoft, t.bg, 4.5],
      ["on-hero on hero", t.onHero, t.hero, 7], ["hero-soft on hero", t.heroSoft, t.hero, 4.5],
      ["on-primary on primary", t.onPrimary, t.primary, 4.5], ["primary on surface", t.primary, t.surface, 4.5],
      ["ceil-ink on ceil", t.ceilInk, t.ceil, 4.5],
      ...["Done", "Review", "Schedule", "Urgent"].map((s) => [`hero ${s} lane on hero`, t[`hero${s}`], t.hero, 3]),
      ...["done", "review", "schedule", "urgent"].flatMap((s) => [
        [`${s} chip`, t[s], t[`${s}Soft`], 4.5], [`${s} on surface`, t[s], t.surface, 4.5],
      ]),
    ];
    for (const [label, fg, bg, min] of checks) {
      const r = ratio(fg, bg);
      assert.ok(r >= min, `${label}: ${r.toFixed(2)} < ${min}`);
    }
  });
}
