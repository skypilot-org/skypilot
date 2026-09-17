'use client';

import React from 'react';

// Matches a bare http(s) URL. The final character class excludes trailing
// sentence punctuation so a URL that ends a sentence -- "... See
// https://docs.skypilot.co/...#anchor." -- does not pull the period into the
// href. No `g` flag: String.split does not need it, and a stateful regex
// shared across renders is a footgun.
const URL_REGEX = /(https?:\/\/[^\s<>"']*[^\s<>"'.,;:!?)\]}])/;

// Long docs URLs dominate a line of prose. Show host + fragment, which is the
// part that carries meaning, and keep the full URL in the href.
const MAX_VISIBLE_URL = 48;

function displayUrl(url) {
  if (url.length <= MAX_VISIBLE_URL) {
    return url;
  }
  try {
    const u = new URL(url);
    return `${u.host}${u.hash}`;
  } catch {
    return `${url.slice(0, MAX_VISIBLE_URL - 1)}…`;
  }
}

/**
 * Renders `text` with any bare http(s) URL turned into a real link.
 *
 * Remediation hints (e.g. the Kubernetes OOM hints) carry a docs URL with a
 * section anchor. Rendered as plain text that anchor cannot be followed, which
 * defeats the point of pointing at a specific section.
 *
 * Newlines are preserved: these messages are composed server-side with line
 * breaks separating the diagnosis from the remedy, and collapsing them turns
 * the whole thing into one unreadable paragraph.
 */
export function LinkifiedText({ text, className }) {
  const safeText = text == null ? '' : String(text);
  if (!safeText) {
    return null;
  }
  // String.split with a capturing group interleaves the captures at the odd
  // indices, so parity tells us which parts are URLs.
  const parts = safeText.split(URL_REGEX);
  return (
    <span className={className} style={{ whiteSpace: 'pre-wrap' }}>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <a
            key={i}
            href={part}
            title={part}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-600 hover:text-blue-800 underline"
          >
            {displayUrl(part)}
          </a>
        ) : (
          part
        )
      )}
    </span>
  );
}
