import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/router';
import {
  buildQueryString,
  filtersToQuery,
  hasLegacyFilters,
  legacyFiltersToQuery,
  queryToFilters,
  withoutLegacyKeys,
} from '@/components/shared/filterSchema';

/**
 * Keep a page's filter chips and view state in the URL, as named parameters.
 *
 * The URL is the source of truth on load, and is rewritten (never pushed) as
 * the user filters, so a link can be shared or bookmarked but filtering does
 * not fill the back button. A link still carrying the legacy
 * `property`/`operator`/`value` triples is translated once on arrival and the
 * address bar is rewritten to the named form.
 *
 * `filterSchema` describes the filterable properties; see filterSchema.js.
 * `viewSchema` describes non-filter state that belongs in the URL too, as
 * `{ key, default }` — anything equal to its default is left out of the URL.
 *
 * Writes go through `history.replaceState` rather than `router.replace` for
 * the reason clusters.jsx and jobs.jsx already do it that way: a router write
 * re-renders the page and cascades into the data hooks below it.
 */
export function useUrlFilterState(filterSchema, viewSchema = []) {
  const router = useRouter();

  const readQuery = useCallback(() => {
    if (typeof window === 'undefined') {
      return {};
    }
    const params = new URLSearchParams(window.location.search);
    const query = {};
    for (const key of new Set(params.keys())) {
      const all = params.getAll(key);
      query[key] = all.length > 1 ? all : all[0];
    }
    return query;
  }, []);

  const readInitial = useCallback(() => {
    const query = readQuery();
    const named = hasLegacyFilters(query)
      ? {
          ...withoutLegacyKeys(query),
          ...legacyFiltersToQuery(filterSchema, query),
        }
      : query;
    const view = {};
    for (const entry of viewSchema) {
      // A view entry may declare `fromLegacy` to migrate an older spelling of
      // itself -- e.g. the clusters page used to carry `history=true` plus a
      // separate `historyDays=N`. It wins over the raw value, since the raw
      // value is what it is migrating away from.
      const migrated = entry.fromLegacy ? entry.fromLegacy(query) : undefined;
      const raw = migrated !== undefined ? migrated : named[entry.key];
      view[entry.key] = raw === undefined ? entry.default : String(raw);
    }
    return { filters: queryToFilters(filterSchema, named), view };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readQuery]);

  const initial = useRef(null);
  if (initial.current === null) {
    initial.current = { ...readInitial(), query: readQuery() };
  }

  const [filters, setFilters] = useState(initial.current.filters);
  const [view, setViewState] = useState(initial.current.view);

  // Adopt the query whenever it changes from outside this hook. Two cases:
  // a hard load, where a statically exported page hydrates before Next parses
  // the query; and a same-route navigation carrying filters, e.g. a link built
  // by `buildFilterUrl` pointing back at the page it is rendered on.
  //
  // Writes this hook makes go through `history.replaceState`, which leaves
  // `router.asPath` alone, so remembering what we last wrote is what tells our
  // own writes apart from someone else's navigation.
  const lastWritten = useRef(null);
  useEffect(() => {
    if (!router.isReady || typeof window === 'undefined') {
      return;
    }
    if (window.location.search === lastWritten.current) {
      return;
    }
    const next = readInitial();
    setFilters((prev) =>
      JSON.stringify(prev) === JSON.stringify(next.filters)
        ? prev
        : next.filters
    );
    setViewState((prev) =>
      JSON.stringify(prev) === JSON.stringify(next.view) ? prev : next.view
    );
  }, [router.isReady, router.asPath, readInitial]);

  // Mirror state into the address bar. Keys not owned by this hook (a plugin's
  // own params, `tab`, ...) are preserved.
  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }
    const owned = new Set([
      ...filterSchema.map((e) => e.key),
      ...viewSchema.map((e) => e.key),
      ...viewSchema.flatMap((e) => e.legacyKeys || []),
      'property',
      'operator',
      'value',
    ]);
    const current = readQuery();
    const query = {};
    for (const [key, value] of Object.entries(current)) {
      if (!owned.has(key)) {
        query[key] = value;
      }
    }
    for (const entry of viewSchema) {
      const value = view[entry.key];
      if (value !== undefined && value !== null && value !== entry.default) {
        query[entry.key] = String(value);
      }
    }
    Object.assign(query, filtersToQuery(filterSchema, filters));

    const search = buildQueryString(query);
    // Record it either way: when the URL already matches, our state and the
    // address bar agree, and the adopt-external effect must not treat that as
    // someone else's navigation.
    lastWritten.current = search;
    const next = `${window.location.pathname}${search}${window.location.hash}`;
    if (
      next !==
      `${window.location.pathname}${window.location.search}${window.location.hash}`
    ) {
      // Preserve the existing state: Next.js keeps its router entry there
      // (`__N`, `key`, the resolved url), and replacing it with null makes a
      // later popstate look like a non-Next navigation -- the address bar
      // changes while the rendered page does not.
      window.history.replaceState(window.history.state, '', next);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, view, readQuery]);

  const setView = useCallback((key, value) => {
    setViewState((prev) =>
      prev[key] === value ? prev : { ...prev, [key]: value }
    );
  }, []);

  // The query as it was on arrival, before this hook rewrote the address bar.
  // Pages that need to know whether something was deep-linked must read this
  // rather than `router.query`: the rewrite can land before Next parses the
  // query on a statically exported page, and the two would disagree.
  return {
    filters,
    setFilters,
    view,
    setView,
    initialQuery: initial.current.query,
  };
}
