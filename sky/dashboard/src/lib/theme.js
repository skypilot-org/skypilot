// Theme constants and helpers shared by the ThemeProvider and the inline
// no-flash script in pages/_document.js. Keep this file free of React/DOM
// imports: _document.js loads it on the server.
//
// Theme contract for plugins and other consumers:
//   - `<html>` has the `dark` class when the resolved theme is dark.
//   - `<html data-theme="light|dark">` always holds the resolved theme.
//   - Colors are CSS variables in src/app/globals.css (hsl channels, e.g.
//     `hsl(var(--surface))`) that change under `.dark`.

export const THEME_STORAGE_KEY = 'skypilot-dashboard-theme';
export const THEME_LIGHT = 'light';
export const THEME_DARK = 'dark';
export const THEME_SYSTEM = 'system';
export const THEMES = [THEME_LIGHT, THEME_DARK, THEME_SYSTEM];

export const isTheme = (value) => THEMES.includes(value);

export const resolveTheme = (theme, systemPrefersDark) => {
  if (theme === THEME_SYSTEM) {
    return systemPrefersDark ? THEME_DARK : THEME_LIGHT;
  }
  return theme;
};

export const applyResolvedTheme = (resolved) => {
  const root = document.documentElement;
  root.classList.toggle('dark', resolved === THEME_DARK);
  root.dataset.theme = resolved;
};

// Runs in <head> before first paint so a dark user never sees a light flash.
// Mirrors resolveTheme/applyResolvedTheme above; any failure (e.g. storage
// blocked) falls back to the light default.
export const NO_FLASH_SCRIPT = `(function(){try{var t=localStorage.getItem(${JSON.stringify(
  THEME_STORAGE_KEY
)});var d=t==='dark'||(t!=='light'&&window.matchMedia('(prefers-color-scheme: dark)').matches);var r=document.documentElement;r.classList.toggle('dark',d);r.dataset.theme=d?'dark':'light';}catch(e){}})();`;
