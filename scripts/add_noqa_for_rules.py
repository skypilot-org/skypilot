#!/usr/bin/env python3
"""Add noqa comments for specified ruff rules to existing violations.

This script finds violations of specified rules and adds noqa comments
to suppress them, preserving existing code behavior during migration.

Usage:
    python scripts/add_noqa_for_rules.py A001,A002,SLF001,PLC0415
"""

import subprocess
import sys
import json
from collections import defaultdict


def get_violations(rules: str, path: str = 'sky/') -> list[dict]:
    """Get violations from ruff for specified rules."""
    result = subprocess.run(
        ['ruff', 'check', path, '--select', rules, '--output-format', 'json'],
        capture_output=True,
        text=True
    )
    if result.stdout:
        return json.loads(result.stdout)
    return []


def add_noqa_comments(violations: list[dict]) -> dict[str, int]:
    """Add noqa comments to files for given violations."""
    # Group violations by file and line
    by_file_line = defaultdict(set)
    for v in violations:
        filepath = v['filename']
        line = v['location']['row']
        code = v['code']
        by_file_line[(filepath, line)].add(code)

    # Group by file for processing
    by_file = defaultdict(dict)
    for (filepath, line), codes in by_file_line.items():
        by_file[filepath][line] = codes

    stats = defaultdict(int)

    for filepath, line_codes in by_file.items():
        with open(filepath, 'r') as f:
            lines = f.readlines()

        modified = False
        for line_num, codes in sorted(line_codes.items(), reverse=True):
            idx = line_num - 1
            if idx >= len(lines):
                continue

            line = lines[idx].rstrip('\n')
            codes_str = ', '.join(sorted(codes))

            # Check if line already has a noqa comment
            if '# noqa:' in line:
                # Parse existing noqa and add new codes
                noqa_idx = line.index('# noqa:')
                before_noqa = line[:noqa_idx].rstrip()
                after_noqa = line[noqa_idx + 7:].strip()

                # Extract existing codes
                existing_codes = set()
                comment_rest = ''
                if '#' in after_noqa:
                    codes_part, comment_rest = after_noqa.split('#', 1)
                    comment_rest = '  #' + comment_rest
                else:
                    codes_part = after_noqa

                for code in codes_part.replace(',', ' ').split():
                    existing_codes.add(code.strip())

                # Add new codes
                all_codes = existing_codes | codes
                new_codes_str = ', '.join(sorted(all_codes))
                lines[idx] = f'{before_noqa}  # noqa: {new_codes_str}{comment_rest}\n'
                modified = True
                for code in codes:
                    stats[code] += 1
            elif '# noqa' in line and '# noqa:' not in line:
                # Has blanket noqa, skip
                continue
            else:
                # Add new noqa comment
                # Preserve any existing comment
                if '  #' in line:
                    # Has inline comment, insert noqa before it
                    comment_idx = line.index('  #')
                    before_comment = line[:comment_idx]
                    comment = line[comment_idx:]
                    lines[idx] = f'{before_comment}  # noqa: {codes_str}{comment}\n'
                elif ' #' in line and not line.strip().startswith('#'):
                    # Has inline comment with single space
                    comment_idx = line.index(' #')
                    before_comment = line[:comment_idx]
                    comment = line[comment_idx:]
                    lines[idx] = f'{before_comment}  # noqa: {codes_str}{comment}\n'
                else:
                    lines[idx] = f'{line}  # noqa: {codes_str}\n'
                modified = True
                for code in codes:
                    stats[code] += 1

        if modified:
            with open(filepath, 'w') as f:
                f.writelines(lines)

    return stats


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/add_noqa_for_rules.py RULE1,RULE2,...")
        print("Example: python scripts/add_noqa_for_rules.py A001,A002,SLF001,PLC0415")
        sys.exit(1)

    rules = sys.argv[1]
    path = sys.argv[2] if len(sys.argv) > 2 else 'sky/'

    print(f"Finding violations for rules: {rules}")
    violations = get_violations(rules, path)

    if not violations:
        print("No violations found!")
        return

    print(f"Found {len(violations)} violations")

    stats = add_noqa_comments(violations)

    print("\nAdded noqa comments:")
    for code, count in sorted(stats.items()):
        print(f"  {code}: {count}")
    print(f"\nTotal: {sum(stats.values())} comments added")


if __name__ == '__main__':
    main()
