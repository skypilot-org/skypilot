/**
 * Hook for managing dismissed (soft-deleted) items in the dashboard.
 *
 * Persists dismissed item IDs in localStorage so they survive page refreshes.
 * Items are only hidden from view — never deleted from the backend database.
 *
 * @param {string} storageKey - localStorage key (must be stable across renders),
 *   e.g. 'sky-dismissed-clusters'
 * @returns {Object} Dismissed items state and actions
 */
import { useState, useCallback, useEffect } from 'react';

function loadDismissedIds(storageKey) {
  if (typeof window === 'undefined') return new Set();
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      return new Set(parsed.filter((id) => typeof id === 'string' && id));
    }
    return new Set();
  } catch (e) {
    console.warn(
      `Failed to load dismissed items from localStorage key "${storageKey}":`,
      e
    );
    return new Set();
  }
}

function saveDismissedIds(storageKey, idSet) {
  if (typeof window === 'undefined') return;
  try {
    localStorage.setItem(storageKey, JSON.stringify([...idSet]));
  } catch (e) {
    // localStorage may be full or disabled; log for observability.
    console.warn(
      `Failed to save dismissed items to localStorage key "${storageKey}":`,
      e
    );
  }
}

export function useDismissedItems(storageKey) {
  const [dismissedIds, setDismissedIds] = useState(() =>
    loadDismissedIds(storageKey)
  );

  // Reload from localStorage if storageKey changes (defensive).
  useEffect(() => {
    setDismissedIds(loadDismissedIds(storageKey));
  }, [storageKey]);

  // Sync to localStorage whenever the set changes
  useEffect(() => {
    saveDismissedIds(storageKey, dismissedIds);
  }, [storageKey, dismissedIds]);

  const dismissItem = useCallback((id) => {
    setDismissedIds((prev) => {
      const next = new Set(prev);
      next.add(String(id));
      return next;
    });
  }, []);

  const restoreItem = useCallback((id) => {
    setDismissedIds((prev) => {
      const next = new Set(prev);
      next.delete(String(id));
      return next;
    });
  }, []);

  const isDismissed = useCallback(
    (id) => dismissedIds.has(String(id)),
    [dismissedIds]
  );

  const clearAllDismissed = useCallback(() => {
    setDismissedIds(new Set());
  }, []);

  const filterDismissed = useCallback(
    (items, idGetter) => {
      if (dismissedIds.size === 0) return items;
      return items.filter((item) => !dismissedIds.has(String(idGetter(item))));
    },
    [dismissedIds]
  );

  return {
    dismissItem,
    restoreItem,
    isDismissed,
    clearAllDismissed,
    filterDismissed,
    dismissedCount: dismissedIds.size,
  };
}
