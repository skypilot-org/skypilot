'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { stripAnsiCodes } from '@/components/utils';
import { getDashboardConfig } from '@/data/connectors/dashboard_config';

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
 * not break the page.
 *
 * @param {Array<{label: string, regex: string}>} externalLinks
 * @returns {Object<string, RegExp>}
 */
export const compileCustomPatterns = (externalLinks) => {
  const compiled = {};
  if (!Array.isArray(externalLinks)) return compiled;
  for (const entry of externalLinks) {
    if (
      !entry ||
      typeof entry.label !== 'string' ||
      typeof entry.regex !== 'string'
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
 * @returns {Object<string, RegExp>}
 */
export const useCustomUrlPatterns = () => {
  const [patterns, setPatterns] = useState(BUILTIN_URL_PATTERNS);

  useEffect(() => {
    let cancelled = false;
    getDashboardConfig()
      .then((config) => {
        if (cancelled) return;
        const compiled = compileCustomPatterns(config?.externalLinks);
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
  }, []);

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
 * @returns {{extractedLinks: Object<string, string>,
 *            scanLines: (lines: string[]|string) => void}}
 */
export const useLogLinkExtractor = () => {
  const urlPatterns = useCustomUrlPatterns();
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
