// Dedupe a strict allowlist of idempotent dashboard GETs that are fetched
// from many independent bundles (dashboard core + plugin frontends).
// In-flight coalescing eliminates the duplicates that fire within the same
// tick; a short TTL absorbs later calls triggered by route changes and
// late-mounting providers.

// Suffix match lets this work for any ENDPOINT base path (e.g.
// `/internal/dashboard`, or a custom basePath in multi-tenant deploys).
const TTL_MS = {
  '/internal/dashboard/api/health': 30_000,
  '/internal/dashboard/api/plugins': 60_000,
  '/internal/dashboard/users/role': 30_000,
};

const cache = new Map();
const inflight = new Map();

function matchPath(input) {
  try {
    // fetch() accepts a string, a Request (has .url), or a URL (has .href).
    const urlStr =
      typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.href
          : input && input.url;
    if (!urlStr) return null;
    const u = new URL(urlStr, window.location.origin);
    for (const suffix of Object.keys(TTL_MS)) {
      if (u.pathname.endsWith(suffix)) return suffix;
    }
    return null;
  } catch {
    return null;
  }
}

function responseFromCached(entry) {
  return new Response(entry.body, {
    status: entry.status,
    headers: entry.headersEntries,
  });
}

export function installFetchDedupe() {
  if (typeof window === 'undefined') return;
  if (window.__skyFetchDedupeInstalled) return;
  window.__skyFetchDedupeInstalled = true;

  const origFetch = window.fetch.bind(window);

  window.fetch = async function dedupedFetch(input, init) {
    const method = (
      (init && init.method) ||
      (typeof input === 'object' && input && input.method) ||
      'GET'
    ).toUpperCase();
    if (method !== 'GET') return origFetch(input, init);

    const path = matchPath(input);
    if (!path) return origFetch(input, init);

    const now = Date.now();
    const cached = cache.get(path);
    if (cached && now - cached.ts < TTL_MS[path]) {
      return responseFromCached(cached);
    }

    const pending = inflight.get(path);
    if (pending) return pending.then((r) => r.clone());

    const p = (async () => {
      const resp = await origFetch(input, init);
      if (resp.ok) {
        try {
          const body = await resp.clone().text();
          cache.set(path, {
            body,
            status: resp.status,
            headersEntries: Array.from(resp.headers.entries()),
            ts: Date.now(),
          });
        } catch {
          // Body unreadable (streamed/consumed); skip caching, still
          // return the original response to the caller.
        }
      }
      return resp;
    })().finally(() => {
      inflight.delete(path);
    });

    inflight.set(path, p);
    return p.then((r) => r.clone());
  };
}
