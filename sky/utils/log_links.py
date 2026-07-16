"""Extract external links from job logs (server/controller side).

This is the Python counterpart of
``sky/dashboard/src/utils/externalLinks.js``. The dashboard renders external
links from the database (authoritative) and only falls back to its own
client-side scan for clusters too old to populate the DB. To keep the two in
agreement, the tokenization, ANSI stripping, and the built-in W&B pattern here
MUST stay identical to the JS implementation.

Two responsibilities are intentionally separated:

- ``extract_candidate_urls`` harvests URL-shaped tokens with no knowledge of
  patterns or config. It is safe to run on a worker skylet (which does not have
  the admin ``dashboard.external_links`` config).
- ``get_patterns``/``match_links``/``extract_links_from_lines`` do the pattern
  matching against the live config. These run on the controller / API server,
  so matching happens in exactly one place against one config source.
"""
import re
from typing import Dict, Iterable, List, Optional, Pattern

from sky import sky_logging
from sky import skypilot_config

logger = sky_logging.init_logger(__name__)

# Key under which the worker skylet stashes harvested candidate URLs in the
# local jobs.db ``metadata`` JSON. The controller / API server read this key and
# match the URLs against the configured patterns.
EXTRACTED_URLS_METADATA_KEY = 'extracted_urls'

# Mirror of BUILTIN_URL_PATTERNS in externalLinks.js. Anchored so a token must
# be exactly a W&B run URL (and not, e.g., the project page).
BUILTIN_PATTERNS: Dict[str, str] = {
    # Matches W&B SaaS (wandb.ai) and dedicated tenants (<tenant>.wandb.io).
    'W&B Run': (r'^https://(?:wandb\.ai|[^/]+\.wandb\.io)'
                r'/[^/]+/[^/]+/runs/[^/]+$'),
}

# Mirror of stripAnsiCodes() in dashboard/src/components/utils.jsx.
_ANSI_RE = re.compile(r'\x1b\[([0-9]{1,2}(;[0-9]{1,2})?)?[mGKH]')

# Mirror of the token split in extractLinksFromLogs(): whitespace plus the
# common delimiters that bracket a URL in log output.
_TOKEN_SPLIT_RE = re.compile(r'[\s"\'<>()\[\]{},;]+')

# Trailing punctuation stripped from each token (mirror of JS).
_TRAILING_PUNCT_RE = re.compile(r'[.,:;!?]+$')

# Only http(s) tokens are candidate URLs (used by the worker harvest, which has
# no patterns to test against).
_URL_PREFIX_RE = re.compile(r'^https?://')

# Bound the number of distinct candidate URLs retained by the worker harvest so
# a chatty log cannot bloat ``jobs.metadata``. Links of interest are printed
# early in a run, so insertion order keeps them. The controller's terminal scan
# reads the complete (uncapped) log, so this cap never affects final
# correctness, only how quickly a link shows up live.
DEFAULT_CANDIDATE_CAP = 200


def _strip_ansi(line: str) -> str:
    return _ANSI_RE.sub('', line)


def extract_candidate_urls(
    lines: Iterable[str],
    existing: Optional[List[str]] = None,
    cap: int = DEFAULT_CANDIDATE_CAP,
) -> List[str]:
    """Return distinct http(s) URL tokens found in the given log lines.

    No pattern matching or config is required, so this is safe to run on a
    worker skylet. Tokenization mirrors ``extractLinksFromLogs`` in the
    dashboard.

    Args:
        lines: Log lines to scan.
        existing: URLs already harvested (preserved and de-duplicated against).
        cap: Maximum number of distinct URLs to retain.

    Returns:
        Distinct URL tokens in first-seen order.
    """
    urls: List[str] = list(existing or [])
    seen = set(urls)
    for line in lines:
        if len(urls) >= cap:
            break
        if not isinstance(line, str):
            continue
        for token in _TOKEN_SPLIT_RE.split(_strip_ansi(line)):
            clean = _TRAILING_PUNCT_RE.sub('', token)
            if not clean or not _URL_PREFIX_RE.match(clean):
                continue
            if clean not in seen:
                seen.add(clean)
                urls.append(clean)
                if len(urls) >= cap:
                    break
    return urls


def compile_admin_patterns(entries: Optional[list]) -> Dict[str, Pattern]:
    """Compile ``dashboard.external_links`` entries into a label -> regex map.

    Mirrors ``compileCustomPatterns`` in externalLinks.js. Invalid regexes are
    skipped with a warning so one bad entry does not break extraction.
    """
    compiled: Dict[str, Pattern] = {}
    if not isinstance(entries, list):
        return compiled
    for entry in entries:
        if (not isinstance(entry, dict) or
                not isinstance(entry.get('label'), str) or
                not isinstance(entry.get('regex'), str)):
            continue
        try:
            compiled[entry['label']] = re.compile(entry['regex'])
        except re.error as e:
            logger.warning(
                'Skipping dashboard.external_links entry with invalid regex '
                f'for label {entry["label"]!r}: {e}')
    return compiled


def get_patterns() -> Dict[str, Pattern]:
    """Built-in patterns plus admin-configured ``dashboard.external_links``.

    Admin entries override built-ins on a label collision, matching the
    dashboard's merge order.
    """
    patterns: Dict[str, Pattern] = {
        label: re.compile(regex) for label, regex in BUILTIN_PATTERNS.items()
    }
    entries = skypilot_config.get_nested(('dashboard', 'external_links'), [])
    patterns.update(compile_admin_patterns(entries))
    return patterns


def match_links(candidate_urls: Iterable[str],
                patterns: Dict[str, Pattern]) -> Dict[str, str]:
    """Match candidate URLs against patterns, returning a label -> url map.

    For each pattern, the first candidate URL that matches wins (mirroring the
    first-match-wins behavior of ``extractLinksFromLogs``). Used by the live
    path, where the controller already has URL tokens harvested by the worker.
    """
    links: Dict[str, str] = {}
    if not patterns:
        return links
    for url in candidate_urls:
        if len(links) == len(patterns):
            break
        for label, pattern in patterns.items():
            if label in links:
                continue
            # ``pattern.search`` mirrors JS ``RegExp.test`` (unanchored); the
            # built-in pattern carries its own ^...$ anchors.
            if pattern.search(url):
                links[label] = url
                break
    return links


def extract_links_from_lines(
    lines: Iterable[str],
    patterns: Dict[str, Pattern],
    existing: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Scan log lines and return a label -> url map of matched links.

    Faithful port of ``extractLinksFromLogs`` in externalLinks.js: tokenize each
    line, test each token against every not-yet-matched pattern, and stop early
    once every pattern has matched. Used by the controller's terminal-state scan
    of a complete downloaded log.
    """
    links: Dict[str, str] = dict(existing or {})
    if not patterns:
        return links
    found = set(links.keys())
    for line in lines:
        if len(found) == len(patterns):
            break
        if not isinstance(line, str):
            continue
        for token in _TOKEN_SPLIT_RE.split(_strip_ansi(line)):
            clean = _TRAILING_PUNCT_RE.sub('', token)
            if not clean:
                continue
            for label, pattern in patterns.items():
                if label in found:
                    continue
                if pattern.search(clean):
                    links[label] = clean
                    found.add(label)
                    break
    return links
