/**
 * Hook for managing dismissed (soft-deleted) items in the dashboard.
 *
 * Persists dismissed item IDs to the backend database so they survive across browsers/devices.
 * Optimistically updates the UI for immediate feedback and reverts on API failure.
 * Items are only hidden from view — never deleted from the core tables.
 *
 * @param {string} storageKey - The type of items being dismissed
 *   e.g. 'sky-dismissed-clusters'
 * @returns {Object} Dismissed items state and actions
 */
import { useState, useCallback, useEffect, useRef } from 'react';
import { apiClient } from '@/data/connectors/client';

export function useDismissedItems(storageKey) {
  // We use the storageKey as the itemType for the backend (e.g., 'sky-dismissed-clusters')
  const itemType = storageKey;

  const [dismissedIds, setDismissedIds] = useState(new Set());

  // Ref to track the current state to avoid stale closures in optimistic updates
  const dismissedIdsRef = useRef(dismissedIds);
  useEffect(() => {
    dismissedIdsRef.current = dismissedIds;
  }, [dismissedIds]);

  // Load from backend on mount or when storageKey changes
  useEffect(() => {
    let isMounted = true;

    async function fetchDismissed() {
      try {
        const ids = await apiClient.fetch(
          `/dashboard/dismissed_items/get`,
          { item_type: itemType },
          'POST'
        );
        if (isMounted && Array.isArray(ids)) {
          setDismissedIds(new Set(ids.map(String)));
        }
      } catch (e) {
        console.warn(`Failed to fetch dismissed items for ${itemType}:`, e);
      }
    }

    fetchDismissed();

    return () => {
      isMounted = false;
    };
  }, [itemType]);

  const dismissItem = useCallback(
    async (id) => {
      const stringId = String(id);

      // Optimistic update
      setDismissedIds((prev) => {
        const next = new Set(prev);
        next.add(stringId);
        return next;
      });

      try {
        await apiClient.fetch(
          '/dashboard/dismissed_items/add',
          {
            item_type: itemType,
            item_id: stringId,
          },
          'POST'
        );
      } catch (e) {
        console.warn(`Failed to dismiss item ${stringId}:`, e);
        // Revert on failure
        setDismissedIds((prev) => {
          const next = new Set(prev);
          next.delete(stringId);
          return next;
        });
      }
    },
    [itemType]
  );

  const restoreItem = useCallback(
    async (id) => {
      const stringId = String(id);

      // Optimistic update
      setDismissedIds((prev) => {
        const next = new Set(prev);
        next.delete(stringId);
        return next;
      });

      try {
        await apiClient.fetch(
          '/dashboard/dismissed_items/remove',
          {
            item_type: itemType,
            item_id: stringId,
          },
          'POST'
        );
      } catch (e) {
        console.warn(`Failed to restore item ${stringId}:`, e);
        // Revert on failure
        setDismissedIds((prev) => {
          const next = new Set(prev);
          next.add(stringId);
          return next;
        });
      }
    },
    [itemType]
  );

  const isDismissed = useCallback(
    (id) => dismissedIds.has(String(id)),
    [dismissedIds]
  );

  const clearAllDismissed = useCallback(async () => {
    // Save previous state for revert
    const prevIds = new Set(dismissedIdsRef.current);

    // Optimistic update
    setDismissedIds(new Set());

    try {
      await apiClient.fetch(
        '/dashboard/dismissed_items/clear_all',
        {
          item_type: itemType,
          item_id: '', // Not used but required by body schema
        },
        'POST'
      );
    } catch (e) {
      console.warn(`Failed to clear all dismissed items:`, e);
      // Revert on failure
      setDismissedIds(prevIds);
    }
  }, [itemType]);

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
