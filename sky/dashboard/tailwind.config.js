/** @type {import('tailwindcss').Config} */
const extraContent = (process.env.SKYPILOT_DASHBOARD_TAILWIND_CONTENT || '')
  .split(';')
  .map((s) => s.trim())
  .filter(Boolean);

// Semantic color tokens backed by CSS variables in src/app/globals.css, which
// swap under the `.dark` class. `<alpha-value>` keeps opacity modifiers
// (bg-surface/50) working.
const token = (name) => `hsl(var(--${name}) / <alpha-value>)`;
// Status/accent colors and the roles the dashboard uses of each (see the
// matching variables in globals.css). e.g. red.tint -> bg-red-tint,
// red.fg -> text-red-fg. Extending (not replacing) the default palette keeps
// classes like text-red-500 working for plugins, but those do not follow the
// theme. To use a role not listed here, add it here and in globals.css.
const HUE_ROLES = {
  red: 'tint tint-strong line solid-soft solid solid-hover fg-faint fg-soft fg fg-strong fg-stronger',
  green:
    'tint tint-strong line line-strong solid-soft solid solid-hover fg-faint fg-soft fg fg-strong fg-stronger fg-strongest',
  yellow: 'tint tint-strong line solid-soft fg-soft fg fg-strong fg-stronger',
  amber:
    'tint tint-strong line solid solid-hover fg-faint fg-soft fg fg-strong fg-stronger fg-strongest',
  orange: 'tint fg fg-strong',
  purple: 'tint tint-strong fg fg-strong fg-stronger',
  indigo: 'tint fg-soft fg fg-strong',
  cyan: 'tint fg-strong',
  pink: 'tint fg-strong',
  emerald: 'tint line solid fg-soft fg fg-strong',
  teal: 'tint',
  blue: 'tint tint-strong line line-strong solid-soft solid solid-hover fg-faint fg-soft fg fg-strong fg-stronger fg-strongest',
  sky: 'tint tint-strong line solid-soft solid solid-hover fg-soft fg fg-strong fg-stronger fg-strongest',
};
const hueTokens = Object.fromEntries(
  Object.entries(HUE_ROLES).map(([hue, list]) => [
    hue,
    Object.fromEntries(list.split(' ').map((r) => [r, token(`${hue}-${r}`)])),
  ])
);

module.exports = {
  darkMode: ['class'],
  content: [
    './pages/**/*.{js,jsx}',
    './components/**/*.{js,jsx}',
    './app/**/*.{js,jsx}',
    './src/**/*.{js,jsx}',
    ...extraContent,
  ],
  prefix: '',
  theme: {
    container: {
      center: true,
      padding: '2rem',
      screens: {
        '2xl': '1400px',
      },
    },
    extend: {
      colors: {
        gcpgreen: '#188038',
        'sky-blue': '#0E2E65',
        'sky-blue-bright': '#1E62CC',
        ...hueTokens,
        surface: {
          DEFAULT: token('surface'),
          subtle: token('surface-subtle'),
          muted: token('surface-muted'),
          strong: token('surface-strong'),
          stronger: token('surface-stronger'),
          log: token('surface-log'),
          code: token('surface-code'),
        },
        content: {
          DEFAULT: token('content'),
          strong: token('content-strong'),
          heavy: token('content-heavy'),
          secondary: token('content-secondary'),
          tertiary: token('content-tertiary'),
          faint: token('content-faint'),
          faintest: token('content-faintest'),
        },
        line: {
          DEFAULT: token('line'),
          subtle: token('line-subtle'),
          strong: token('line-strong'),
          stronger: token('line-stronger'),
        },
        brand: {
          DEFAULT: token('brand'),
          bright: token('brand-bright'),
          solid: token('brand-solid'),
          'solid-hover': token('brand-solid-hover'),
        },
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      keyframes: {
        'accordion-down': {
          from: { height: '0' },
          to: { height: 'var(--radix-accordion-content-height)' },
        },
        'accordion-up': {
          from: { height: 'var(--radix-accordion-content-height)' },
          to: { height: '0' },
        },
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out',
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
};
