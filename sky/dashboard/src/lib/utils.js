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
