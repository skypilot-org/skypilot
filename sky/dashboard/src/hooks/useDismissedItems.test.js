/**
 * Tests for the useDismissedItems hook.
 *
 * Verifies localStorage-based soft-delete of dashboard items.
 */

import { renderHook, act } from '@testing-library/react';
import { useDismissedItems } from './useDismissedItems';

// Mock localStorage with configurable property to avoid leaking into other tests
const localStorageMock = (() => {
  let store = {};
  return {
    getItem: jest.fn((key) => store[key] || null),
    setItem: jest.fn((key, value) => {
      store[key] = value;
    }),
    removeItem: jest.fn((key) => {
      delete store[key];
    }),
    clear: jest.fn(() => {
      store = {};
    }),
    _getStore: () => store,
  };
})();

const originalLocalStorage = globalThis.localStorage;

beforeAll(() => {
  Object.defineProperty(globalThis, 'localStorage', {
    value: localStorageMock,
    configurable: true,
    writable: true,
  });
});

afterAll(() => {
  Object.defineProperty(globalThis, 'localStorage', {
    value: originalLocalStorage,
    configurable: true,
    writable: true,
  });
});

describe('useDismissedItems', () => {
  beforeEach(() => {
    localStorageMock.clear();
    jest.clearAllMocks();
  });

  test('starts with zero dismissed items', () => {
    const { result } = renderHook(() => useDismissedItems('test-dismissed'));
    expect(result.current.dismissedCount).toBe(0);
  });

  test('dismissItem adds an item', () => {
    const { result } = renderHook(() => useDismissedItems('test-dismissed'));

    act(() => {
      result.current.dismissItem('item-1');
    });

    expect(result.current.dismissedCount).toBe(1);
    expect(result.current.isDismissed('item-1')).toBe(true);
    expect(result.current.isDismissed('item-2')).toBe(false);
  });

  test('dismissItem handles numeric ids by converting to string', () => {
    const { result } = renderHook(() => useDismissedItems('test-dismissed'));

    act(() => {
      result.current.dismissItem(42);
    });

    expect(result.current.isDismissed('42')).toBe(true);
    expect(result.current.isDismissed(42)).toBe(true);
  });

  test('restoreItem removes a dismissed item', () => {
    const { result } = renderHook(() => useDismissedItems('test-dismissed'));

    act(() => {
      result.current.dismissItem('item-1');
      result.current.dismissItem('item-2');
    });

    expect(result.current.dismissedCount).toBe(2);

    act(() => {
      result.current.restoreItem('item-1');
    });

    expect(result.current.dismissedCount).toBe(1);
    expect(result.current.isDismissed('item-1')).toBe(false);
    expect(result.current.isDismissed('item-2')).toBe(true);
  });

  test('clearAllDismissed removes all items', () => {
    const { result } = renderHook(() => useDismissedItems('test-dismissed'));

    act(() => {
      result.current.dismissItem('a');
      result.current.dismissItem('b');
      result.current.dismissItem('c');
    });

    expect(result.current.dismissedCount).toBe(3);

    act(() => {
      result.current.clearAllDismissed();
    });

    expect(result.current.dismissedCount).toBe(0);
    expect(result.current.isDismissed('a')).toBe(false);
  });

  test('filterDismissed removes dismissed items from array', () => {
    const { result } = renderHook(() => useDismissedItems('test-dismissed'));

    const items = [
      { id: '1', name: 'Alpha' },
      { id: '2', name: 'Beta' },
      { id: '3', name: 'Gamma' },
    ];

    act(() => {
      result.current.dismissItem('2');
    });

    const filtered = result.current.filterDismissed(items, (item) => item.id);
    expect(filtered).toHaveLength(2);
    expect(filtered.map((i) => i.name)).toEqual(['Alpha', 'Gamma']);
  });

  test('filterDismissed returns all items when nothing is dismissed', () => {
    const { result } = renderHook(() => useDismissedItems('test-dismissed'));

    const items = [
      { id: '1', name: 'Alpha' },
      { id: '2', name: 'Beta' },
    ];

    const filtered = result.current.filterDismissed(items, (item) => item.id);
    expect(filtered).toHaveLength(2);
  });

  test('persists to localStorage', () => {
    const { result } = renderHook(() => useDismissedItems('test-key'));

    act(() => {
      result.current.dismissItem('item-x');
    });

    // The effect should have called setItem
    expect(localStorageMock.setItem).toHaveBeenCalledWith(
      'test-key',
      expect.any(String)
    );

    const lastCall = localStorageMock.setItem.mock.calls;
    const stored = JSON.parse(lastCall[lastCall.length - 1][1]);
    expect(stored).toContain('item-x');
  });

  test('loads from localStorage on init', () => {
    // Set data directly in the store so it persists through the
    // storageKey-change useEffect reload.
    localStorageMock.setItem(
      'preloaded-key',
      JSON.stringify(['pre-1', 'pre-2'])
    );
    // Clear mock call tracking so we only assert on hook behavior
    localStorageMock.setItem.mockClear();

    const { result } = renderHook(() => useDismissedItems('preloaded-key'));

    expect(result.current.dismissedCount).toBe(2);
    expect(result.current.isDismissed('pre-1')).toBe(true);
    expect(result.current.isDismissed('pre-2')).toBe(true);
  });

  test('handles invalid localStorage data gracefully', () => {
    // Set invalid JSON directly in the store
    localStorageMock.setItem('bad-data-key', 'not-valid-json{{{');
    localStorageMock.setItem.mockClear();

    const { result } = renderHook(() => useDismissedItems('bad-data-key'));

    // Should not crash, should start empty
    expect(result.current.dismissedCount).toBe(0);
  });

  test('handles non-array localStorage data gracefully', () => {
    // Set non-array JSON directly in the store
    localStorageMock.setItem('object-data-key', JSON.stringify({ key: 'val' }));
    localStorageMock.setItem.mockClear();

    const { result } = renderHook(() => useDismissedItems('object-data-key'));

    expect(result.current.dismissedCount).toBe(0);
  });

  test('does not add duplicate items', () => {
    const { result } = renderHook(() => useDismissedItems('test-dismissed'));

    act(() => {
      result.current.dismissItem('same');
      result.current.dismissItem('same');
      result.current.dismissItem('same');
    });

    expect(result.current.dismissedCount).toBe(1);
  });
});
