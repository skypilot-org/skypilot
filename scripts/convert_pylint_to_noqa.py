#!/usr/bin/env python3
"""Convert pylint disable comments to ruff noqa comments.

This script converts:
  # pylint: disable=line-too-long  ->  # noqa: E501

Usage:
    python scripts/convert_pylint_to_noqa.py [--dry-run]
"""
import argparse
import re
import sys
from pathlib import Path

# Mapping of pylint disable names to ruff noqa codes
PYLINT_TO_NOQA = {
    'line-too-long': 'E501',
}


def convert_line(line: str) -> str:
    """Convert pylint disable comments to noqa comments on a single line."""
    for pylint_name, noqa_code in PYLINT_TO_NOQA.items():
        # Match patterns like:
        #   # pylint: disable=line-too-long
        #   # pylint:disable=line-too-long
        #   #pylint: disable=line-too-long
        pattern = rf'#\s*pylint:\s*disable={re.escape(pylint_name)}'
        replacement = f'# noqa: {noqa_code}'
        line = re.sub(pattern, replacement, line)
    return line


def process_file(filepath: Path, dry_run: bool = False) -> int:
    """Process a single file. Returns number of lines changed."""
    try:
        content = filepath.read_text()
    except Exception as e:
        print(f'Error reading {filepath}: {e}', file=sys.stderr)
        return 0

    lines = content.splitlines(keepends=True)
    changed_lines = 0
    new_lines = []

    for i, line in enumerate(lines, 1):
        new_line = convert_line(line)
        if new_line != line:
            changed_lines += 1
            if dry_run:
                print(f'{filepath}:{i}')
                print(f'  - {line.rstrip()}')
                print(f'  + {new_line.rstrip()}')
        new_lines.append(new_line)

    if changed_lines > 0 and not dry_run:
        filepath.write_text(''.join(new_lines))

    return changed_lines


def main():
    parser = argparse.ArgumentParser(
        description='Convert pylint disable comments to ruff noqa comments')
    parser.add_argument('--dry-run',
                        action='store_true',
                        help='Show changes without modifying files')
    args = parser.parse_args()

    sky_dir = Path(__file__).parent.parent / 'sky'
    if not sky_dir.exists():
        print(f'Error: {sky_dir} does not exist', file=sys.stderr)
        sys.exit(1)

    total_files = 0
    total_changes = 0

    for filepath in sky_dir.rglob('*.py'):
        changes = process_file(filepath, dry_run=args.dry_run)
        if changes > 0:
            total_files += 1
            total_changes += changes

    action = 'Would change' if args.dry_run else 'Changed'
    print(f'\n{action} {total_changes} lines in {total_files} files')


if __name__ == '__main__':
    main()
