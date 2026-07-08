import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

// Dev/QA helper: when the dashboard URL carries `?empty=1` (or just `?empty`),
// data tables render their empty state even when data exists, so empty-state
// styling can be reviewed against a populated server. No effect otherwise.
export function isForceEmpty() {
  if (typeof window === 'undefined') return false;
  return new URLSearchParams(window.location.search).has('empty');
}

// Reads a persisted "rows per page" value from localStorage. Keyed by
// storageKey so each table remembers its own choice independently. SSR-safe
// and defensive: returns fallback when running on the server, when nothing is
// stored, when the stored value is not a valid number, or when it is not one
// of validOptions (guards against stale/tampered values). Reading localStorage
// can also throw (e.g. Safari private mode), which we swallow.
export function getPersistedPageSize(storageKey, validOptions, fallback = 10) {
  if (typeof window === 'undefined') return fallback;
  try {
    const stored = parseInt(window.localStorage.getItem(storageKey), 10);
    return validOptions.includes(stored) ? stored : fallback;
  } catch (e) {
    return fallback;
  }
}

// Persists a "rows per page" value to localStorage under storageKey. SSR-safe
// and swallows write errors (e.g. storage disabled or quota exceeded).
export function persistPageSize(storageKey, value) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(storageKey, String(value));
  } catch (e) {
    // Ignore: persistence is best-effort.
  }
}
