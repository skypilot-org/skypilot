"""Slurm hostlist expansion.

Slurm reports node sets in a compressed "hostlist" notation, e.g.
``node[01-03,07],gpu-a,rack[1-2]n[1-4]``. This module expands that notation
into individual host names, mirroring ``scontrol show hostnames`` for the
shapes Slurm itself emits, so callers don't need the Slurm CLI (which may not
exist inside containers) or a third-party package.

Supported grammar:

* Top-level entries are comma separated. Commas inside ``[...]`` belong to
  the range list.
* A ``[...]`` group is a comma separated list of ``N`` or ``LO-HI`` numeric
  ranges. Zero padding is preserved when the range start has a leading zero
  (``[01-3]`` -> ``01, 02, 03``; ``[8-11]`` -> ``8, 9, 10, 11``).
* An entry may contain several groups; they expand as a cartesian product in
  order (``r[1-2]n[1-2]`` -> ``r1n1, r1n2, r2n1, r2n2``).

Duplicates are dropped while preserving first-seen order. Malformed input
raises :class:`HostlistError` (a ``ValueError``).
"""
import itertools
import re
from typing import Dict, List

# Refuse to expand absurdly large expressions such as ``n[1-999999999]``
# rather than exhausting memory.
MAX_HOSTS = 100_000

_GROUP_RE = re.compile(r'\[([^\[\]]*)\]')
_RANGE_RE = re.compile(r'(\d+)(?:-(\d+))?')


class HostlistError(ValueError):
    """Raised for a malformed hostlist expression."""


def expand_hostlist(expr: str) -> List[str]:
    """Expands a Slurm hostlist expression into a list of host names.

    Example: ``expand_hostlist('n[9-11],d[01-02]')`` ->
    ``['n9', 'n10', 'n11', 'd01', 'd02']``.

    Args:
        expr: The hostlist expression. ``None`` or an empty/blank string
            expands to an empty list.

    Returns:
        The expanded host names, deduplicated, in order of first appearance.

    Raises:
        HostlistError: If the expression is malformed (unbalanced brackets,
            a non-numeric or reversed range) or would expand to more than
            ``MAX_HOSTS`` names.
    """
    if expr is None:
        return []
    expr = expr.strip()
    if not expr:
        return []

    hosts: List[str] = []
    seen = set()
    for entry in _split_entries(expr):
        for host in _expand_entry(entry):
            if host in seen:
                continue
            seen.add(host)
            hosts.append(host)
            if len(hosts) > MAX_HOSTS:
                raise HostlistError(
                    f'Hostlist expands to more than {MAX_HOSTS} hosts: '
                    f'{expr!r}')
    return hosts


def _split_entries(expr: str) -> List[str]:
    """Splits on commas that are outside ``[...]`` groups."""
    entries: List[str] = []
    depth = 0
    current: List[str] = []
    for ch in expr:
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth < 0:
                raise HostlistError(f'Unbalanced brackets in hostlist {expr!r}')
        if ch == ',' and depth == 0:
            entries.append(''.join(current))
            current = []
        else:
            current.append(ch)
    entries.append(''.join(current))
    if depth != 0:
        raise HostlistError(f'Unbalanced brackets in hostlist {expr!r}')
    # Stray commas ("a,,b", ",a") are tolerated, matching scontrol.
    return [entry for entry in entries if entry]


def _expand_entry(entry: str) -> List[str]:
    """Expands one comma-free entry, e.g. ``rack[1-2]n[01-02]``."""
    # re.split with a capturing group alternates literal text and the inside
    # of each bracket group: ['rack', '1-2', 'n', '01-02', ''].
    pieces = _GROUP_RE.split(entry)
    literals = pieces[0::2]
    specs = pieces[1::2]
    for literal in literals:
        if '[' in literal or ']' in literal:
            raise HostlistError(f'Unbalanced brackets in hostlist {entry!r}')
    if not specs:
        return [entry]

    choices: List[List[str]] = []
    total = 1
    for literal, spec in zip(literals, specs):
        values = _expand_range_list(spec, entry)
        total *= len(values)
        if total > MAX_HOSTS:
            raise HostlistError(
                f'Hostlist expands to more than {MAX_HOSTS} hosts: {entry!r}')
        choices.append([literal + value for value in values])
    choices.append([literals[-1]])
    return [''.join(parts) for parts in itertools.product(*choices)]


def _expand_range_list(spec: str, entry: str) -> List[str]:
    """Expands the inside of one bracket group, e.g. ``01-03,07``.

    Values that repeat across the group's ranges (``1-5,3-8``) are returned
    once, so the size guard counts distinct hosts.
    """
    values: Dict[str, None] = {}
    for item in spec.split(','):
        item = item.strip()
        match = _RANGE_RE.fullmatch(item)
        if match is None:
            raise HostlistError(
                f'Bad range {item!r} in hostlist entry {entry!r}')
        low, high = match.group(1), match.group(2)
        if high is None:
            values[low] = None
            continue
        low_int, high_int = int(low), int(high)
        if low_int > high_int:
            raise HostlistError(
                f'Range start > stop in {item!r} (hostlist entry {entry!r})')
        if high_int - low_int + 1 > MAX_HOSTS:
            raise HostlistError(
                f'Hostlist expands to more than {MAX_HOSTS} hosts: {entry!r}')
        # Slurm keeps the zero padding of the range start: [01-3] -> 01,02,03.
        width = len(low) if low.startswith('0') else 0
        for i in range(low_int, high_int + 1):
            values[str(i).zfill(width)] = None
    return list(values)
