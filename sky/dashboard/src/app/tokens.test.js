/**
 * Guards the theme tokens in globals.css: every variable the Tailwind config
 * references is defined for both themes, and text/background pairs keep WCAG
 * AA contrast in dark mode (and in light mode for neutrals, brand and badge
 * text). Light-mode `*-solid` fills and `*-fg` text of some colors (green,
 * amber, sky, ...) are Tailwind 600 shades that were already below 4.5:1.
 */
const fs = require('fs');
const path = require('path');
const { theme } = require('../../tailwind.config.js');

const css = fs.readFileSync(path.join(__dirname, 'globals.css'), 'utf8');
const vars = (selector) =>
  Object.fromEntries(
    [
      ...css
        .match(new RegExp(`${selector}\\s*\\{([\\s\\S]*?)\\n  \\}`))[1]
        .matchAll(/--([\w-]+):\s*([\d.]+) ([\d.]+)% ([\d.]+)%;/g),
    ].map((m) => [m[1], [+m[2], +m[3], +m[4]]])
  );
const light = vars(':root');
const dark = { ...light, ...vars('\\.dark') };

const luminance = ([h, s, l]) => {
  s /= 100;
  l /= 100;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => {
    const k = (n + h / 30) % 12;
    const c = l - a * Math.max(-1, Math.min(k - 3, Math.min(9 - k, 1)));
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(0) + 0.7152 * f(8) + 0.0722 * f(4);
};
const contrast = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
const WHITE = [0, 0, 100];
const colors = theme.extend.colors;
const hues = Object.keys(colors).filter((name) => colors[name].tint);

// [foreground, background] pairs checked when both roles exist for a color.
const BADGE = [['fg-strong', 'tint']];
const DARK_ONLY = [
  ['fg', 'tint'],
  ['fg', 'tint-strong'],
  ['fg-stronger', 'tint-strong'],
];

test('every variable the Tailwind config uses is defined in both themes', () => {
  const used = [...JSON.stringify(colors).matchAll(/var\(--([\w-]+)\)/g)].map(
    (m) => m[1]
  );
  const darkOwn = vars('\\.dark');
  expect(used.filter((name) => !(name in light))).toEqual([]);
  expect(used.filter((name) => !(name in darkOwn))).toEqual([]);
});

describe.each([
  ['light', light],
  ['dark', dark],
])('%s neutrals and brand', (_, t) => {
  const pairs = [];
  for (const bg of ['surface', 'surface-subtle', 'surface-muted']) {
    for (const fg of ['content-strong', 'content', 'content-secondary']) {
      pairs.push([fg, bg]);
    }
  }
  pairs.push(
    ['content-tertiary', 'surface'],
    ['content-tertiary', 'surface-subtle'],
    ['brand', 'surface'],
    ['brand-bright', 'surface']
  );
  test.each(pairs)('%s on %s is AA', (fg, bg) => {
    expect(contrast(t[fg], t[bg])).toBeGreaterThanOrEqual(4.5);
  });
});

describe.each(hues)('%s', (hue) => {
  const has = (role) => `${hue}-${role}` in dark;
  const aa = (t, fg, bg) =>
    contrast(t[`${hue}-${fg}`], bg in t ? t[bg] : t[`${hue}-${bg}`]);

  test('badge text is AA in both themes', () => {
    for (const [fg, bg] of BADGE.filter(([f, b]) => has(f) && has(b))) {
      expect(aa(light, fg, bg)).toBeGreaterThanOrEqual(4.5);
      expect(aa(dark, fg, bg)).toBeGreaterThanOrEqual(4.5);
    }
  });

  test('dark: text, solid fills and hover fills are AA', () => {
    for (const [fg, bg] of DARK_ONLY.filter(([f, b]) => has(f) && has(b))) {
      expect(aa(dark, fg, bg)).toBeGreaterThanOrEqual(4.5);
    }
    if (has('fg'))
      expect(aa(dark, 'fg', 'surface')).toBeGreaterThanOrEqual(4.5);
    for (const role of ['solid', 'solid-hover'].filter(has)) {
      expect(contrast(WHITE, dark[`${hue}-${role}`])).toBeGreaterThanOrEqual(
        4.5
      );
    }
  });
});

test('dark brand fills keep white text AA', () => {
  for (const name of ['brand-solid', 'brand-solid-hover']) {
    expect(contrast(WHITE, dark[name])).toBeGreaterThanOrEqual(4.5);
  }
});
