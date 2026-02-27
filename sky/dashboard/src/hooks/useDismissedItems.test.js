/**
 * Tests for the useDismissedItems hook.
 *
 * Verifies API-backed soft-delete of dashboard items.
 */

import { renderHook, act, waitFor } from '@testing-library/react';
import { useDismissedItems } from './useDismissedItems';
import { apiClient } from '@/data/connectors/client';

// Mock the apiClient
jest.mock('@/data/connectors/client', () => ({
  apiClient: {
    fetch: jest.fn(),
  },
}));

describe('useDismissedItems', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('starts with zero dismissed items when API returns empty', async () => {
    apiClient.fetch.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useDismissedItems('test-dismissed'));

    expect(result.current.dismissedCount).toBe(0);
    expect(apiClient.fetch).toHaveBeenCalledWith(
      '/dashboard/dismissed_items/test-dismissed',
      {},
      'GET'
    );
  });

  test('dismissItem optimistically adds an item and calls API', async () => {
    apiClient.fetch.mockResolvedValueOnce([]); // initial load
    apiClient.fetch.mockResolvedValueOnce({}); // dismiss call

    const { result } = renderHook(() => useDismissedItems('test-dismissed'));

    act(() => {
      result.current.dismissItem('item-1');
    });

    expect(result.current.dismissedCount).toBe(1);
    expect(result.current.isDismissed('item-1')).toBe(true);

    expect(apiClient.fetch).toHaveBeenCalledWith(
      '/dashboard/dismissed_items/add',
      { item_type: 'test-dismissed', item_id: 'item-1' },
      'POST'
    );
  });

  test('dismissItem reverts on API failure', async () => {
    apiClient.fetch.mockResolvedValueOnce([]); // initial load

    const { result } = renderHook(() => useDismissedItems('test-key'));

    // Reject the add call
    apiClient.fetch.mockRejectedValueOnce(new Error('Network error'));

    await act(async () => {
      await result.current.dismissItem('item-fail');
    });

    // Should be reverted back to 0
    expect(result.current.dismissedCount).toBe(0);
    expect(result.current.isDismissed('item-fail')).toBe(false);
  });

  test('restoreItem removes a dismissed item optimistically and calls API', async () => {
    apiClient.fetch.mockResolvedValueOnce(['item-1', 'item-2']); // initial load
    apiClient.fetch.mockResolvedValueOnce({}); // restore call

    const { result } = renderHook(() => useDismissedItems('test-dismissed'));

    // Wait for initial load
    await waitFor(() => {
      expect(result.current.dismissedCount).toBe(2);
    });

    act(() => {
      result.current.restoreItem('item-1');
    });

    expect(result.current.dismissedCount).toBe(1);
    expect(result.current.isDismissed('item-1')).toBe(false);
    expect(result.current.isDismissed('item-2')).toBe(true);

    expect(apiClient.fetch).toHaveBeenCalledWith(
      '/dashboard/dismissed_items/remove',
      { item_type: 'test-dismissed', item_id: 'item-1' },
      'POST'
    );
  });

  test('restoreItem reverts on API failure', async () => {
    apiClient.fetch.mockResolvedValueOnce(['item-1']); // initial load

    const { result } = renderHook(() => useDismissedItems('test-key'));

    await waitFor(() => {
      expect(result.current.dismissedCount).toBe(1);
    });

    apiClient.fetch.mockRejectedValueOnce(new Error('Network error'));

    await act(async () => {
      await result.current.restoreItem('item-1');
    });

    // Should be reverted back to 1
    expect(result.current.dismissedCount).toBe(1);
    expect(result.current.isDismissed('item-1')).toBe(true);
  });

  test('clearAllDismissed removes all items optimistically and calls API', async () => {
    apiClient.fetch.mockResolvedValueOnce(['a', 'b', 'c']); // initial load
    apiClient.fetch.mockResolvedValueOnce({}); // clear all

    const { result } = renderHook(() => useDismissedItems('test-dismissed'));

    await waitFor(() => {
      expect(result.current.dismissedCount).toBe(3);
    });

    act(() => {
      result.current.clearAllDismissed();
    });

    expect(result.current.dismissedCount).toBe(0);
    expect(result.current.isDismissed('a')).toBe(false);

    expect(apiClient.fetch).toHaveBeenCalledWith(
      '/dashboard/dismissed_items/clear_all',
      { item_type: 'test-dismissed', item_id: '' },
      'POST'
    );
  });

  test('loads from API on init', async () => {
    apiClient.fetch.mockResolvedValueOnce(['pre-1', 'pre-2']);

    const { result } = renderHook(() => useDismissedItems('preloaded-key'));

    await waitFor(() => {
      expect(result.current.dismissedCount).toBe(2);
    });

    expect(result.current.isDismissed('pre-1')).toBe(true);
    expect(result.current.isDismissed('pre-2')).toBe(true);
  });

  test('filterDismissed removes dismissed items from array', async () => {
    apiClient.fetch.mockResolvedValueOnce(['2']);

    const { result } = renderHook(() => useDismissedItems('test-dismissed'));

    await waitFor(() => {
      expect(result.current.dismissedCount).toBe(1);
    });

    const items = [
      { id: '1', name: 'Alpha' },
      { id: '2', name: 'Beta' },
      { id: '3', name: 'Gamma' },
    ];

    const filtered = result.current.filterDismissed(items, (item) => item.id);
    expect(filtered).toHaveLength(2);
    expect(filtered.map((i) => i.name)).toEqual(['Alpha', 'Gamma']);
  });
});
