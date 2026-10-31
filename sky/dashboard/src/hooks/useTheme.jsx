import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import PropTypes from 'prop-types';
import {
  THEME_DARK,
  THEME_LIGHT,
  THEME_STORAGE_KEY,
  THEME_SYSTEM,
  applyResolvedTheme,
  isTheme,
  resolveTheme,
} from '@/lib/theme';

const DARK_QUERY = '(prefers-color-scheme: dark)';

const ThemeContext = createContext({
  theme: THEME_SYSTEM,
  resolvedTheme: THEME_LIGHT,
  setTheme: () => {},
});

const readStoredTheme = () => {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isTheme(stored) ? stored : THEME_SYSTEM;
  } catch (e) {
    return THEME_SYSTEM;
  }
};

export function ThemeProvider({ children }) {
  // The server render has no storage; nothing rendered below depends on the
  // value before mount, so reading it lazily on the client is safe.
  const [theme, setThemeState] = useState(() =>
    typeof window === 'undefined' ? THEME_SYSTEM : readStoredTheme()
  );
  const [systemDark, setSystemDark] = useState(() =>
    typeof window === 'undefined'
      ? false
      : window.matchMedia(DARK_QUERY).matches
  );

  useEffect(() => {
    const media = window.matchMedia(DARK_QUERY);
    const onMediaChange = (e) => setSystemDark(e.matches);
    // Keep other open dashboard tabs in sync.
    const onStorage = (e) => {
      if (e.key === THEME_STORAGE_KEY) setThemeState(readStoredTheme());
    };
    setSystemDark(media.matches);
    media.addEventListener('change', onMediaChange);
    window.addEventListener('storage', onStorage);
    return () => {
      media.removeEventListener('change', onMediaChange);
      window.removeEventListener('storage', onStorage);
    };
  }, []);

  const resolvedTheme = resolveTheme(theme, systemDark);

  useEffect(() => {
    applyResolvedTheme(resolvedTheme);
  }, [resolvedTheme]);

  const setTheme = useCallback((next) => {
    if (!isTheme(next)) return;
    setThemeState(next);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch (e) {
      // Storage blocked: the choice still applies for this page view.
    }
  }, []);

  const value = useMemo(
    () => ({ theme, resolvedTheme, setTheme }),
    [theme, resolvedTheme, setTheme]
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

ThemeProvider.propTypes = {
  children: PropTypes.node,
};

export const useTheme = () => useContext(ThemeContext);

export { THEME_DARK, THEME_LIGHT, THEME_SYSTEM };
