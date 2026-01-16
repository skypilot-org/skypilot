#!/usr/bin/env python3
"""Convert pylint disable comments to ruff noqa comments or remove them.

This script handles three categories of pylint comments:
1. Convert to noqa: Rules that are enforced by Ruff
2. Remove entirely: Rules not enforced by Ruff or already in ignore list
3. Special cases: skip-file, multiple disables, etc.

Usage:
    python scripts/convert_pylint_to_noqa.py [--dry-run]
"""
import argparse
import re
import sys
from pathlib import Path

# Mapping of pylint disable names to ruff noqa codes
# Only for rules that ARE enforced by Ruff
PYLINT_TO_NOQA = {
    'line-too-long': 'E501',
    'anomalous-backslash-in-string': 'W605',
    'invalid-string-quote': 'Q000',
    'inconsistent-quotes': 'Q000',
}

# Pylint rules that should be removed entirely (not enforced by Ruff)
# These comments served a purpose with pylint but are not needed with Ruff
RULES_TO_REMOVE = {
    # Broad exception rules - not checked by Ruff by default
    'broad-except',
    'broad-exception-caught',
    'W0703',  # Same as broad-except
    # Import rules - not checked by Ruff
    'import-outside-toplevel',
    'wrong-import-position',  # E402 is per-file ignored where needed
    'ungrouped-imports',  # Handled by isort
    # Naming rules - N rules not enabled
    'invalid-name',
    # Access rules - not checked by Ruff
    'protected-access',
    # Redefinition rules - not checked by Ruff
    'redefined-builtin',  # A rules not enabled
    'redefined-outer-name',
    # Argument rules - ARG rules not enabled
    'unused-argument',
    'unused-import',  # F401 is per-file ignored where needed
    'unused-variable',  # F841 - should be caught but removing comment is safe
    # Type checking rules - handled by mypy, not Ruff
    'not-callable',
    'abstract-class-instantiated',
    'assignment-from-no-return',
    'unsubscriptable-object',
    'E1136',  # Same as unsubscriptable-object
    'E0110',  # abstract-class-instantiated
    # Rules in Ruff's ignore list
    'unnecessary-lambda',  # E731 in ignore
    'raise-missing-from',  # B904 in ignore
    # Style rules not enforced
    'bad-docstring-quotes',  # D rules not enabled
    'pointless-statement',  # B018 not in select
    'consider-using-with',  # SIM115 not in select
    'cell-var-from-loop',  # B023 not in select
    'unspecified-encoding',  # Not checked
    'try-except-raise',  # TRY rules not enabled
    'superfluous-parens',  # Not important
    'super-init-not-called',  # Not checked
    'C0206',  # consider-using-dict-items - not checked
    'W0622',  # redefined-builtin (numeric code)
    'C0415',  # import-outside-toplevel (numeric code)
}


def convert_line(line: str) -> tuple[str, str]:
    """Convert or remove pylint disable comments on a single line.

    Returns: (new_line, action) where action is one of:
        - 'converted': Comment was converted to noqa
        - 'removed': Comment was removed entirely
        - 'skip-file': Line contains skip-file directive
        - 'unchanged': No pylint comment or unknown rule
    """
    # Check for skip-file directive
    if re.search(r'#\s*pylint:\s*skip-file', line):
        # Replace with ruff's file-level ignore
        new_line = re.sub(r'#\s*pylint:\s*skip-file', '# ruff: noqa', line)
        return new_line, 'skip-file'

    # Handle # pylint: enable=... (just remove, not needed with noqa)
    if re.search(r'#\s*pylint:\s*enable=', line):
        new_line = re.sub(r'\s*#\s*pylint:\s*enable=[\w,-]*\s*', '', line)
        if new_line.strip() == '':
            return '', 'removed'
        new_line = new_line.rstrip() + '\n' if line.endswith('\n') else new_line.rstrip()
        return new_line, 'removed'

    # Handle # pylint: disable-next=... (just remove, not needed with noqa)
    if re.search(r'#\s*pylint:\s*disable-next=', line):
        new_line = re.sub(r'\s*#\s*pylint:\s*disable-next=[\w,-]*\s*', '', line)
        if new_line.strip() == '':
            return '', 'removed'
        new_line = new_line.rstrip() + '\n' if line.endswith('\n') else new_line.rstrip()
        return new_line, 'removed'

    # Handle empty # pylint: disable= (often for line-too-long)
    if re.search(r'#\s*pylint:\s*disable=\s*$', line):
        # Assume it was for line-too-long, convert to noqa: E501
        new_line = re.sub(r'#\s*pylint:\s*disable=\s*$', '# noqa: E501', line)
        return new_line, 'converted'

    # Match pylint disable comments
    # Patterns: # pylint: disable=rule or # pylint:disable=rule
    # Handle both comma-separated and space-separated rules
    match = re.search(r'#\s*pylint:\s*disable=\s*([\w\s,-]+)', line)
    if not match:
        return line, 'unchanged'

    rules_str = match.group(1)
    # Split on comma or whitespace
    rules = [r.strip() for r in re.split(r'[,\s]+', rules_str) if r.strip()]

    # Categorize rules
    rules_to_convert = []
    rules_unknown = []

    for rule in rules:
        if rule in PYLINT_TO_NOQA:
            rules_to_convert.append(PYLINT_TO_NOQA[rule])
        elif rule in RULES_TO_REMOVE:
            # Will be removed, no action needed
            pass
        else:
            rules_unknown.append(rule)

    # If there are unknown rules, keep the comment but warn
    if rules_unknown:
        # Keep original line for unknown rules
        return line, 'unchanged'

    # Build replacement
    if rules_to_convert:
        # Convert to noqa with unique codes
        noqa_codes = sorted(set(rules_to_convert))
        replacement = f'# noqa: {", ".join(noqa_codes)}'
        new_line = re.sub(r'#\s*pylint:\s*disable=[\w,-]+', replacement, line)
        return new_line, 'converted'
    else:
        # All rules are in RULES_TO_REMOVE, remove the comment entirely
        # Remove the pylint comment but keep the rest of the line
        new_line = re.sub(r'\s*#\s*pylint:\s*disable=[\w,-]+\s*', '', line)
        # Handle case where comment was on its own line
        if new_line.strip() == '':
            return '', 'removed'
        # Make sure we don't leave trailing whitespace
        new_line = new_line.rstrip() + '\n' if line.endswith('\n') else new_line.rstrip()
        return new_line, 'removed'


def process_file(filepath: Path, dry_run: bool = False) -> dict:
    """Process a single file. Returns counts by action type."""
    try:
        content = filepath.read_text()
    except Exception as e:
        print(f'Error reading {filepath}: {e}', file=sys.stderr)
        return {}

    lines = content.splitlines(keepends=True)
    counts = {'converted': 0, 'removed': 0, 'skip-file': 0}
    new_lines = []
    lines_to_delete = []

    for i, line in enumerate(lines, 1):
        new_line, action = convert_line(line)

        if action == 'unchanged':
            new_lines.append(line)
        elif action == 'removed' and new_line == '':
            # Line should be deleted entirely (was only a pylint comment)
            lines_to_delete.append(i)
            counts['removed'] += 1
            if dry_run:
                print(f'{filepath}:{i} [DELETE LINE]')
                print(f'  - {line.rstrip()}')
        else:
            counts[action] = counts.get(action, 0) + 1
            if dry_run:
                print(f'{filepath}:{i} [{action.upper()}]')
                print(f'  - {line.rstrip()}')
                print(f'  + {new_line.rstrip()}')
            new_lines.append(new_line)

    total_changes = sum(counts.values())
    if total_changes > 0 and not dry_run:
        filepath.write_text(''.join(new_lines))

    return counts


def main():
    parser = argparse.ArgumentParser(
        description='Convert pylint disable comments to ruff noqa or remove them'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show changes without modifying files'
    )
    args = parser.parse_args()

    sky_dir = Path(__file__).parent.parent / 'sky'
    if not sky_dir.exists():
        print(f'Error: {sky_dir} does not exist', file=sys.stderr)
        sys.exit(1)

    total_counts = {'converted': 0, 'removed': 0, 'skip-file': 0}
    files_changed = 0

    for filepath in sky_dir.rglob('*.py'):
        counts = process_file(filepath, dry_run=args.dry_run)
        if counts and sum(counts.values()) > 0:
            files_changed += 1
            for action, count in counts.items():
                total_counts[action] = total_counts.get(action, 0) + count

    action = 'Would change' if args.dry_run else 'Changed'
    print(f'\n{action} {files_changed} files:')
    print(f'  - Converted to noqa: {total_counts["converted"]}')
    print(f'  - Removed entirely: {total_counts["removed"]}')
    print(f'  - Skip-file -> ruff noqa: {total_counts["skip-file"]}')


if __name__ == '__main__':
    main()
