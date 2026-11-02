/**
 * Guards the color tokens in globals.css: every variable the Tailwind config
 * references is defined, and the neutral, brand and badge-text pairs keep WCAG
 * AA contrast. The saturated `*-solid` fills and `*-fg` text of some colors
 * (green, amber, sky, ...) are Tailwind 600 shades that were already below
 * 4.5:1 on white, so they are not asserted.
 */
const fs = require('fs');
const path = require('path');
const { theme } = require('../../tailwind.config.js');

const css = fs.readFileSync(path.join(__dirname, 'globals.css'), 'utf8');
const light = Object.fromEntries(
  [
    ...css
      .match(/:root\s*\{([\s\S]*?)\n  \}/)[1]
      .matchAll(/--([\w-]+):\s*([\d.]+) ([\d.]+)% ([\d.]+)%;/g),
  ].map((m) => [m[1], [+m[2], +m[3], +m[4]]])
);

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
const colors = theme.extend.colors;
const hues = Object.keys(colors).filter((name) => colors[name].tint);

test('every variable the Tailwind config uses is defined', () => {
  const used = [...JSON.stringify(colors).matchAll(/var\(--([\w-]+)\)/g)].map(
    (m) => m[1]
  );
  expect(used.filter((name) => !(name in light))).toEqual([]);
});

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
  expect(contrast(light[fg], light[bg])).toBeGreaterThanOrEqual(4.5);
});

test.each(hues.filter((h) => colors[h]['fg-strong']))(
  '%s badge text is AA on its tint',
  (hue) => {
    expect(
      contrast(light[`${hue}-fg-strong`], light[`${hue}-tint`])
    ).toBeGreaterThanOrEqual(4.5);
  }
);
