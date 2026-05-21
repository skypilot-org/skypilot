import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/router';

/**
 * Bind a piece of React state to a single URL query-string key on the
 * current top-level page.
 *
 * Behaviour:
 *   - When the URL contains the key on mount, the initial state comes from
 *     the URL via `parse`.
 *   - When the state changes to a non-default value, the URL is updated via
 *     `router.replace(..., { shallow: true })` so dependent data fetches
 *     and the browser back-stack are not perturbed.
 *   - When the state matches the default, the key is removed from the URL
 *     so unset state produces clean URLs (e.g. `/jobs` rather than
 *     `/jobs?tab=all&page=1`).
 *
 * The URL writer reads from `window.location.search` rather than
 * `router.query` so that multiple `useUrlState` setters fired in the same
 * React tick compose correctly. `history.replaceState` — which `router.replace`
 * calls under the hood — updates `window.location.search` synchronously,
 * whereas `router.query` only updates on the next render. Reading from
 * `window.location.search` therefore avoids the lost-update race when, for
 * example, a tab button clears both `?jobTab=` and `?status=` at once.
 *
 * Intended for use on top-level pages whose `router.pathname` has no
 * dynamic segments (e.g. `/jobs`, `/clusters`). Routes like `/jobs/[job]`
 * still work for query-string writes but should be wired up case-by-case.
 *
 * @param {string} paramName             query-string key
 * @param {*}      defaultValue          value that maps to "no param in URL"
 * @param {object} [options]
 * @param {(string|string[])=>*} [options.parse]
 *   Convert the raw query value to the in-state representation.
 * @param {(*)=>string|string[]|null} [options.serialize]
 *   Convert state to the URL value. Returning `null` removes the key.
 * @param {(*,*)=>boolean} [options.isDefault]
 *   Equality check against `defaultValue`. Defaults to a shallow check
 *   adequate for primitives and arrays of primitives.
 *
 * @returns {[*, (next:*)=>void]}
 */
export function useUrlState(paramName, defaultValue, options = {}) {
  const {
    parse = (raw) => raw,
    serialize = (value) =>
      Array.isArray(value) ? value.map(String) : String(value),
    isDefault = defaultEquals,
  } = options;

  const router = useRouter();
  const [value, setValue] = useState(defaultValue);
  const hydratedRef = useRef(false);

  // Hydrate once when the router becomes ready. Skipping further runs lets
  // local state changes win over URL changes the user did not initiate
  // (e.g. our own router.replace writes).
  useEffect(() => {
    if (!router.isReady || hydratedRef.current) return;
    hydratedRef.current = true;
    const raw = router.query[paramName];
    if (raw === undefined) {
      setValue(defaultValue);
      return;
    }
    try {
      setValue(parse(raw));
    } catch {
      setValue(defaultValue);
    }
    // parse / defaultValue intentionally excluded — only re-hydrate when the
    // router first becomes ready, not when the consumer re-creates a parse fn.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router.isReady, paramName]);

  const setAndSync = useCallback(
    (next) => {
      setValue((prev) => {
        const resolved = typeof next === 'function' ? next(prev) : next;
        if (typeof window === 'undefined' || !router.isReady) return resolved;
        // Read existing query from window.location.search rather than
        // router.query so multiple setters in the same tick compose without
        // lost-updates (router.query only catches up on the next render).
        const params = new URLSearchParams(window.location.search);
        params.delete(paramName);
        if (!isDefault(resolved, defaultValue)) {
          const serialized = serialize(resolved);
          if (serialized !== null && serialized !== undefined) {
            if (Array.isArray(serialized)) {
              for (const v of serialized) params.append(paramName, v);
            } else {
              params.set(paramName, serialized);
            }
          }
        }
        // Build an object-form query so next/router applies basePath
        // (e.g. "/dashboard") correctly. Passing a string URL would cause
        // router.replace to prepend basePath a second time.
        const queryObj = {};
        for (const key of new Set([...params.keys()])) {
          const vals = params.getAll(key);
          queryObj[key] = vals.length > 1 ? vals : vals[0];
        }
        router.replace(
          { pathname: router.pathname, query: queryObj },
          undefined,
          { shallow: true }
        );
        return resolved;
      });
    },
    [router, paramName, defaultValue, serialize, isDefault]
  );

  return [value, setAndSync];
}

function defaultEquals(a, b) {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
  }
  return false;
}

// ── Common parsers ─────────────────────────────────────────────────────────

/** Parse a query value against an allowed set; falls back to `defaultValue`. */
export function parseEnum(allowed, defaultValue) {
  return (raw) => {
    if (Array.isArray(raw)) raw = raw[0];
    return allowed.includes(raw) ? raw : defaultValue;
  };
}

/**
 * Parse a multi-valued query key into an array of strings, optionally
 * filtered against an allowlist. Single-value keys arrive as a string;
 * repeated keys (`?status=A&status=B`) arrive as an array from next/router.
 */
export function parseStringList(allowed = null) {
  return (raw) => {
    const list = Array.isArray(raw) ? raw : [raw];
    if (!allowed) return list.filter(Boolean);
    return list.filter((v) => allowed.includes(v));
  };
}
