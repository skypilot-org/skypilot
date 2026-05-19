/**
 * Tests for useUrlState — hydration, default-omission, and the URL writer's
 * compose-from-window.location behavior (which is the property that lets
 * multiple setters fire in the same tick without lost-updates).
 */

import { act, renderHook } from '@testing-library/react';
import { useRouter } from 'next/router';

import { parseEnum, parseStringList, useUrlState } from './useUrlState';

jest.mock('next/router', () => ({
  useRouter: jest.fn(),
}));

function mockRouter({ search = '', isReady = true } = {}) {
  const setLocation = (s) => {
    const u = new URL('http://localhost/page' + s);
    delete window.location;
    window.location = u;
  };
  setLocation(search);
  // Build initial query from the search string so the hydration effect sees
  // the same params the writer reads from window.location.search.
  const queryFromSearch = () => {
    const params = new URLSearchParams(window.location.search);
    const q = {};
    for (const key of new Set([...params.keys()])) {
      const vals = params.getAll(key);
      q[key] = vals.length > 1 ? vals : vals[0];
    }
    return q;
  };
  const replace = jest.fn((url) => {
    // Mirror next/router: history.replaceState updates window.location
    // synchronously; router.query catches up on the next render. Accept
    // both string and { pathname, query } forms.
    if (typeof url === 'string') {
      const u = new URL(url, 'http://localhost/');
      setLocation(u.search);
    } else {
      const params = new URLSearchParams();
      for (const [k, v] of Object.entries(url.query || {})) {
        if (Array.isArray(v)) v.forEach((x) => params.append(k, x));
        else if (v !== undefined && v !== null) params.set(k, String(v));
      }
      const s = params.toString();
      setLocation(s ? `?${s}` : '');
    }
    return Promise.resolve(true);
  });
  return {
    pathname: '/page',
    query: queryFromSearch(),
    isReady,
    replace,
  };
}

describe('useUrlState', () => {
  beforeEach(() => {
    delete window.location;
    window.location = new URL('http://localhost/page');
  });

  it('uses the default value when no URL param is set', () => {
    useRouter.mockReturnValue(mockRouter());
    const { result } = renderHook(() => useUrlState('jobTab', 'all'));
    expect(result.current[0]).toBe('all');
  });

  it('hydrates from URL on mount', () => {
    useRouter.mockReturnValue(mockRouter({ search: '?jobTab=finished' }));
    const { result } = renderHook(() =>
      useUrlState('jobTab', 'all', {
        parse: parseEnum(['all', 'active', 'finished'], 'all'),
      })
    );
    expect(result.current[0]).toBe('finished');
  });

  it('writes non-default state to the URL via router.replace', () => {
    const router = mockRouter();
    useRouter.mockReturnValue(router);
    const { result } = renderHook(() => useUrlState('jobTab', 'all'));
    act(() => result.current[1]('finished'));
    expect(router.replace).toHaveBeenCalledWith(
      { pathname: '/page', query: { jobTab: 'finished' } },
      undefined,
      { shallow: true }
    );
  });

  it('removes the key from the URL when state matches the default', () => {
    const router = mockRouter({ search: '?jobTab=finished' });
    useRouter.mockReturnValue(router);
    const { result } = renderHook(() => useUrlState('jobTab', 'all'));
    act(() => result.current[1]('all'));
    expect(router.replace).toHaveBeenCalledWith(
      { pathname: '/page', query: {} },
      undefined,
      { shallow: true }
    );
  });

  it('preserves unrelated query params when writing', () => {
    const router = mockRouter({ search: '?other=keep&jobTab=all' });
    useRouter.mockReturnValue(router);
    const { result } = renderHook(() => useUrlState('jobTab', 'all'));
    act(() => result.current[1]('finished'));
    const writtenQuery = router.replace.mock.calls[0][0].query;
    expect(writtenQuery.other).toBe('keep');
    expect(writtenQuery.jobTab).toBe('finished');
  });

  it('serializes an array as repeated keys', () => {
    const router = mockRouter();
    useRouter.mockReturnValue(router);
    const { result } = renderHook(() =>
      useUrlState('status', [], {
        parse: parseStringList(),
      })
    );
    act(() => result.current[1](['FAILED', 'PENDING']));
    const writtenQuery = router.replace.mock.calls[0][0].query;
    expect(writtenQuery.status).toEqual(['FAILED', 'PENDING']);
  });

  it('hydrates a repeated-key URL into an array', () => {
    useRouter.mockReturnValue(
      mockRouter({ search: '?status=FAILED&status=PENDING' })
    );
    const { result } = renderHook(() =>
      useUrlState('status', [], { parse: parseStringList() })
    );
    expect(result.current[0]).toEqual(['FAILED', 'PENDING']);
  });

  it('lets two setters in the same tick compose via window.location', () => {
    // The race this guards against: setActiveTab + setSelectedStatuses fired
    // together must not let the second writer clobber the first.
    const router = mockRouter({ search: '?jobTab=finished&status=FAILED' });
    useRouter.mockReturnValue(router);
    const tabHook = renderHook(() => useUrlState('jobTab', 'all'));
    const statusHook = renderHook(() =>
      useUrlState('status', [], { parse: parseStringList() })
    );
    act(() => {
      tabHook.result.current[1]('all'); // resets to default → drops jobTab
      statusHook.result.current[1]([]); // resets to default → drops status
    });
    const finalQuery = router.replace.mock.calls.slice(-1)[0][0].query;
    expect(finalQuery).toEqual({});
  });

  it('parseEnum falls back to the default when the value is not allowed', () => {
    useRouter.mockReturnValue(mockRouter({ search: '?jobTab=bogus' }));
    const { result } = renderHook(() =>
      useUrlState('jobTab', 'all', {
        parse: parseEnum(['all', 'active', 'finished'], 'all'),
      })
    );
    expect(result.current[0]).toBe('all');
  });

  it('parseStringList drops values outside the allowlist', () => {
    useRouter.mockReturnValue(
      mockRouter({ search: '?status=FAILED&status=bogus' })
    );
    const { result } = renderHook(() =>
      useUrlState('status', [], {
        parse: parseStringList(['FAILED', 'PENDING']),
      })
    );
    expect(result.current[0]).toEqual(['FAILED']);
  });
});
