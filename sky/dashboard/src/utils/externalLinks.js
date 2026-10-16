'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { stripAnsiCodes } from '@/components/utils';
import {
  EXTERNAL_LINK_SCOPES,
  LINK_SCOPE_CLUSTER,
  LINK_SCOPE_JOBS,
  getDashboardConfig,
} from '@/data/connectors/dashboard_config';

// Re-exported so pages and tests can import everything link-related from
// this module.
export { EXTERNAL_LINK_SCOPES, LINK_SCOPE_CLUSTER, LINK_SCOPE_JOBS };

/**
 * Whether an admin-configured entry may appear on the given page.
 *
 * An entry without a (valid, non-empty) `scope` array appears everywhere;
 * otherwise the page's scope must be listed. A missing `pageScope` means
 * "no filtering" so existing call sites and tests keep their behavior.
 *
 * @param {{scope?: string[]}} entry
 * @param {string|undefined} pageScope One of EXTERNAL_LINK_SCOPES
 * @returns {boolean}
 */
export const isEntryInScope = (entry, pageScope) => {
  if (!pageScope) return true;
  const scope = entry?.scope;
  if (!Array.isArray(scope) || scope.length === 0) return true;
  return scope.includes(pageScope);
};

// Built-in URL patterns that ship with SkyPilot. Admin-configured patterns
// from `dashboard.external_links` are merged on top of these at runtime.
export const BUILTIN_URL_PATTERNS = {
  // Matches W&B SaaS (wandb.ai) and Dedicated Cloud tenants (<tenant>.wandb.io).
  'W&B Run':
    /^https:\/\/(?:wandb\.ai|[^/]+\.wandb\.io)\/[^/]+\/[^/]+\/runs\/[^/]+$/,
};

/**
 * Compile a list of admin-configured patterns into a label -> RegExp map.
 * Invalid regexes are skipped with a console warning so one bad entry does
 * not break the page. Entries scoped to other pages are skipped so their
 * links are never extracted on this page.
 *
 * @param {Array<{label: string, regex: string, scope?: string[]}>} externalLinks
 * @param {string} [pageScope] One of EXTERNAL_LINK_SCOPES; omit to disable
 *   scope filtering.
 * @returns {Object<string, RegExp>}
 */
export const compileCustomPatterns = (externalLinks, pageScope) => {
  const compiled = {};
  if (!Array.isArray(externalLinks)) return compiled;
  for (const entry of externalLinks) {
    if (
      !entry ||
      typeof entry.label !== 'string' ||
      typeof entry.regex !== 'string' ||
      !isEntryInScope(entry, pageScope)
    ) {
      continue;
    }
    try {
      compiled[entry.label] = new RegExp(entry.regex);
    } catch (error) {
      console.warn(
        `Skipping dashboard.external_links entry with invalid regex for label "${entry.label}":`,
        error
      );
    }
  }
  return compiled;
};

/**
 * Scan an array of log lines and return a label -> url map of links that
 * match any of the supplied patterns. The scan tokenizes each line by
 * whitespace and common delimiters and tests each token against every
 * pattern (anchored regexes are expected). Existing matches are preserved
 * and stopping early once every pattern has matched at least once.
 *
 * @param {string[]} logLines
 * @param {Object<string, RegExp>} patterns
 * @param {Object<string, string>} existingMatches Already-found label -> url
 * @returns {Object<string, string>} merged label -> url map
 */
export const extractLinksFromLogs = (logLines, patterns, existingMatches) => {
  const extractedLinks = { ...(existingMatches || {}) };
  const patternEntries = Object.entries(patterns || {});
  if (patternEntries.length === 0) {
    return extractedLinks;
  }
  const foundPatterns = new Set(Object.keys(extractedLinks));

  for (const line of logLines) {
    if (foundPatterns.size === patternEntries.length) {
      break;
    }

    // Plugins can feed arbitrary arrays through `onLogLines`; skip
    // non-string entries instead of letting one bad element throw and
    // take down the page.
    if (typeof line !== 'string') {
      continue;
    }

    // Strip ANSI escape codes so color/reset sequences adjacent to a URL
    // do not leak into the matched token. Lines from the OSS streamer are
    // already stripped (this is a no-op); raw buffers forwarded by log
    // plugins are not.
    const tokens = stripAnsiCodes(line).split(/[\s"'<>()[\]{},;]+/);
    for (const token of tokens) {
      const cleanToken = token.replace(/[.,:;!?]+$/, '');
      if (!cleanToken) continue;

      for (const [label, pattern] of patternEntries) {
        if (foundPatterns.has(label)) continue;
        if (pattern.test(cleanToken)) {
          extractedLinks[label] = cleanToken;
          foundPatterns.add(label);
          break;
        }
      }
    }
  }

  return extractedLinks;
};

/**
 * React hook that returns the merged map of built-in and admin-configured
 * URL patterns. The admin config is fetched once on mount and cached at the
 * connector layer, so subsequent calls reuse the result.
 *
 * @param {string} [pageScope] One of EXTERNAL_LINK_SCOPES; admin entries
 *   scoped to other pages are excluded. Built-in patterns are unscoped.
 * @returns {Object<string, RegExp>}
 */
export const useCustomUrlPatterns = (pageScope) => {
  const [patterns, setPatterns] = useState(BUILTIN_URL_PATTERNS);

  useEffect(() => {
    let cancelled = false;
    getDashboardConfig()
      .then((config) => {
        if (cancelled) return;
        const compiled = compileCustomPatterns(
          config?.externalLinks,
          pageScope
        );
        // Admin patterns are merged on top of built-ins; if a label collides,
        // the admin entry wins so the operator can override defaults.
        setPatterns({ ...BUILTIN_URL_PATTERNS, ...compiled });
      })
      .catch((error) => {
        if (cancelled) return;
        console.debug('useCustomUrlPatterns failed:', error);
      });
    return () => {
      cancelled = true;
    };
  }, [pageScope]);

  return patterns;
};

/**
 * React hook that owns external-link extraction from log lines.
 *
 * Returns `extractedLinks` (an accumulated label -> url map) and
 * `scanLines`, a stable callback that accepts either an array of log
 * lines or a raw newline-separated buffer and scans it against the
 * merged built-in + admin-configured URL patterns. Matches accumulate
 * across calls so they survive tab switches, re-renders, and streaming
 * buffer resets; the most recent lines are re-scanned when the
 * admin-configured patterns finish loading.
 *
 * `scanLines` being a stable callback makes it usable both by the OSS
 * log streamer effects and as a slot-context callback for dashboard
 * plugins that own a logs panel and forward their own lines.
 *
 * @param {string} [pageScope] One of EXTERNAL_LINK_SCOPES; admin patterns
 *   scoped to other pages are not scanned for.
 * @returns {{extractedLinks: Object<string, string>,
 *            scanLines: (lines: string[]|string) => void}}
 */
export const useLogLinkExtractor = (pageScope) => {
  const urlPatterns = useCustomUrlPatterns(pageScope);
  const [extractedLinks, setExtractedLinks] = useState({});
  const extractedLinksRef = useRef({});
  const urlPatternsRef = useRef(urlPatterns);
  const lastLinesRef = useRef(null);

  const scanLines = useCallback((lines) => {
    const lineArray = typeof lines === 'string' ? lines.split('\n') : lines;
    if (!Array.isArray(lineArray) || lineArray.length === 0) {
      return;
    }
    lastLinesRef.current = lineArray;
    const prev = extractedLinksRef.current;
    const next = extractLinksFromLogs(lineArray, urlPatternsRef.current, prev);
    // Matches only ever accumulate, so a size change means new links.
    if (Object.keys(next).length !== Object.keys(prev).length) {
      extractedLinksRef.current = next;
      setExtractedLinks(next);
    }
  }, []);

  useEffect(() => {
    urlPatternsRef.current = urlPatterns;
    // Admin patterns load asynchronously; re-scan the most recent lines
    // so links matching late-arriving patterns are not missed when the
    // stream has already gone quiet (e.g. a finished job).
    if (lastLinesRef.current) {
      scanLines(lastLinesRef.current);
    }
  }, [urlPatterns, scanLines]);

  return { extractedLinks, scanLines };
};

// Variables that may appear as ${var} inside an admin-configured
// `dashboard.external_links` url template. Must stay in sync with
// DASHBOARD_LINK_TEMPLATE_VARIABLES in sky/skypilot_config.py, which
// rejects unknown variables at config load time.
export const TEMPLATE_LINK_VARIABLES = [
  'cluster_name',
  'job_id',
  'job_name',
  'user',
  'workspace',
];

const TEMPLATE_VARIABLE_PATTERN = /\$\{([^}]*)\}/g;

/**
 * Resolve admin-configured url-template entries against a page's metadata.
 *
 * Each `{label, url}` entry has its ${var} placeholders substituted with
 * URI-encoded values from `context`. An entry is skipped (not rendered
 * broken) when any of its variables is missing or empty in the context, so
 * e.g. a ${job_id} link only appears on job pages. Entries scoped to other
 * pages are skipped even when all of their variables resolve, so e.g. a
 * ${cluster_name}-only link can be restricted to the cluster detail page.
 *
 * @param {Array<{label: string, url: string, scope?: string[]}>} externalLinks
 * @param {Object<string, string|number>} context e.g. {cluster_name, job_id}
 * @param {string} [pageScope] One of EXTERNAL_LINK_SCOPES; omit to disable
 *   scope filtering.
 * @returns {Object<string, string>} label -> resolved url map
 */
export const resolveTemplateLinks = (externalLinks, context, pageScope) => {
  const resolved = {};
  if (!Array.isArray(externalLinks)) return resolved;
  const ctx = context || {};
  for (const entry of externalLinks) {
    if (
      !entry ||
      typeof entry.label !== 'string' ||
      typeof entry.url !== 'string' ||
      !isEntryInScope(entry, pageScope)
    ) {
      continue;
    }
    let allResolved = true;
    const url = entry.url.replace(
      TEMPLATE_VARIABLE_PATTERN,
      (_match, variable) => {
        // Only allowlisted variables may be read from the context.
        // Without this, a variable like ${toString} would resolve to an
        // Object.prototype method instead of undefined and serialize
        // function source into the URL.
        if (!TEMPLATE_LINK_VARIABLES.includes(variable)) {
          allResolved = false;
          return '';
        }
        const value = ctx[variable];
        if (value === undefined || value === null || value === '') {
          allResolved = false;
          return '';
        }
        return encodeURIComponent(String(value));
      }
    );
    if (allResolved) {
      resolved[entry.label] = url;
    }
  }
  return resolved;
};

/**
 * React hook that resolves admin-configured url-template links against the
 * supplied page context. The admin config is fetched once on mount and
 * cached at the connector layer. Re-resolves when the context values change
 * (e.g. cluster data finishes loading).
 *
 * @param {Object<string, string|number>} context e.g. {cluster_name, job_id}
 * @param {string} [pageScope] One of EXTERNAL_LINK_SCOPES; entries scoped
 *   to other pages are not resolved.
 * @returns {Object<string, string>} label -> resolved url map
 */
export const useTemplateLinks = (context, pageScope) => {
  const entries = useExternalLinkEntries();

  return useMemo(
    () => resolveTemplateLinks(entries, context, pageScope),
    [entries, context, pageScope]
  );
};

/**
 * React hook that returns the admin-configured `dashboard.external_links`
 * entries (null until the config fetch resolves). The config is fetched
 * once on mount and cached at the connector layer.
 *
 * @returns {Array<Object>|null}
 */
const useExternalLinkEntries = () => {
  const [entries, setEntries] = useState(null);

  useEffect(() => {
    let cancelled = false;
    getDashboardConfig()
      .then((config) => {
        if (cancelled) return;
        setEntries(config?.externalLinks || []);
      })
      .catch((error) => {
        if (cancelled) return;
        console.debug('useExternalLinkEntries failed:', error);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return entries;
};

/**
 * Remove links whose admin-configured entries are all scoped to other
 * pages. Used for label -> url maps whose entries did not pass through
 * compileCustomPatterns/resolveTemplateLinks on this page, i.e. links
 * computed server-side (DB-persisted log matches, instance links). Labels
 * that do not correspond to any configured entry (built-ins, instance
 * links) are kept.
 *
 * @param {Object<string, string>} links label -> url map
 * @param {Array<{label: string, scope?: string[]}>} externalLinks
 * @param {string} [pageScope] One of EXTERNAL_LINK_SCOPES; omit to disable
 *   scope filtering.
 * @returns {Object<string, string>}
 */
export const filterLinksByScope = (links, externalLinks, pageScope) => {
  if (!links) return {};
  if (!pageScope || !Array.isArray(externalLinks)) return links;
  // A label is hidden only when every configured entry carrying it is
  // scoped to other pages; an unscoped or in-scope duplicate keeps it.
  const inScope = new Set();
  const scopedOut = new Set();
  for (const entry of externalLinks) {
    if (!entry || typeof entry.label !== 'string') continue;
    if (isEntryInScope(entry, pageScope)) {
      inScope.add(entry.label);
    } else {
      scopedOut.add(entry.label);
    }
  }
  const filtered = {};
  let changed = false;
  for (const [label, url] of Object.entries(links)) {
    if (scopedOut.has(label) && !inScope.has(label)) {
      changed = true;
      continue;
    }
    filtered[label] = url;
  }
  // Preserve referential identity when nothing was removed so memoized
  // consumers do not re-render.
  return changed ? filtered : links;
};

/**
 * React hook version of filterLinksByScope: filters a label -> url map
 * against the admin-configured entries' scopes for the given page.
 *
 * @param {Object<string, string>} links label -> url map
 * @param {string} [pageScope] One of EXTERNAL_LINK_SCOPES
 * @returns {Object<string, string>}
 */
export const useScopedLinks = (links, pageScope) => {
  const entries = useExternalLinkEntries();

  return useMemo(
    () => filterLinksByScope(links, entries, pageScope),
    [links, entries, pageScope]
  );
};

/**
 * Normalize a URL by ensuring it has an http(s):// protocol prefix.
 *
 * Centralized so cluster, cluster-job, and managed-job pages all render
 * the same href for a given extracted URL.
 *
 * @param {string} url
 * @returns {string}
 */
export const normalizeUrl = (url) => {
  if (!url) return '';
  return url.startsWith('http://') || url.startsWith('https://')
    ? url
    : `https://${url}`;
};
