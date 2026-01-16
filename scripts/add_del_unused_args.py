#!/usr/bin/env python3
"""Add `del` statements for unused method arguments flagged by Ruff ARG002/ARG003.

This script finds all unused method/classmethod arguments and adds
`del arg_name  # Unused` statements at the beginning of the method body.

Usage:
    python scripts/add_del_unused_args.py [--dry-run]
"""
import argparse
import json
import re
import subprocess
from collections import defaultdict


def get_violations():
    """Get ARG002/ARG003 violations from ruff."""
    result = subprocess.run(
        ['ruff', 'check', 'sky/', '--select', 'ARG002,ARG003',
         '--output-format', 'json'],
        capture_output=True, text=True
    )
    if not result.stdout:
        return []
    return json.loads(result.stdout)


def extract_arg_name(message: str) -> str:
    """Extract argument name from message like 'Unused method argument: `foo`'."""
    match = re.search(r'`([^`]+)`', message)
    return match.group(1) if match else ''


def find_def_line(lines: list, start_idx: int) -> int:
    """Find the def line for an argument starting from start_idx going backwards."""
    idx = start_idx
    while idx >= 0:
        if re.match(r'\s*(async\s+)?def\s+', lines[idx]):
            return idx
        idx -= 1
    return -1


def find_method_body_start(lines: list, def_line_idx: int) -> int:
    """Find the line index where the method body starts (after def and docstring)."""
    idx = def_line_idx
    paren_depth = 0
    found_open = False

    # Find end of def statement (may span multiple lines)
    while idx < len(lines):
        line = lines[idx]
        for char in line:
            if char == '(':
                paren_depth += 1
                found_open = True
            elif char == ')':
                paren_depth -= 1

        if found_open and paren_depth == 0:
            if ':' in line.split('#')[0]:
                break
        idx += 1

    idx += 1  # Move to first line of body

    # Skip blank lines and docstrings
    while idx < len(lines):
        stripped = lines[idx].strip()
        if not stripped:
            idx += 1
            continue
        if stripped.startswith('"""') or stripped.startswith("'''"):
            quote = stripped[:3]
            if stripped.count(quote) >= 2:
                idx += 1
            else:
                idx += 1
                while idx < len(lines) and quote not in lines[idx]:
                    idx += 1
                idx += 1
            continue
        break

    return idx


def get_indentation(line: str) -> str:
    """Get the leading whitespace of a line."""
    return line[:len(line) - len(line.lstrip())]


def process_file(filepath: str, violations: list, dry_run: bool = False) -> int:
    """Process a single file and add del statements."""
    with open(filepath, 'r') as f:
        lines = f.readlines()

    # Group violations by their def line
    # First, find the def line for each violation
    methods = defaultdict(set)  # def_line_idx -> set of arg names

    for v in violations:
        arg_name = extract_arg_name(v['message'])
        if not arg_name:
            continue

        # Find the def line for this argument
        arg_line_idx = v['location']['row'] - 1  # Convert to 0-indexed
        def_line_idx = find_def_line(lines, arg_line_idx)

        if def_line_idx < 0:
            print(f"  Warning: Could not find def for arg '{arg_name}' at line {v['location']['row']}")
            continue

        methods[def_line_idx].add(arg_name)

    if not methods:
        return 0

    # Now process each method - find body start and add/update del statement
    # Sort by def_line descending so insertions don't affect line numbers
    insertions = []

    for def_line_idx, args in methods.items():
        body_start = find_method_body_start(lines, def_line_idx)

        if body_start >= len(lines):
            print(f"  Warning: Could not find body for method at line {def_line_idx + 1}")
            continue

        indent = get_indentation(lines[body_start])
        insertions.append((body_start, indent, sorted(args), def_line_idx + 1))

    # Sort by body_start descending
    insertions.sort(key=lambda x: x[0], reverse=True)

    modified_count = 0
    for body_start, indent, args, def_line in insertions:
        # Check if there's already a del statement
        existing_line = lines[body_start].strip() if body_start < len(lines) else ''

        if existing_line.startswith('del '):
            # Parse existing del and merge
            match = re.match(r'(\s*)del\s+([^#\n]+)', lines[body_start])
            if match:
                existing_indent = match.group(1)
                existing_args = [a.strip() for a in match.group(2).split(',')]
                # Add new args
                all_args = list(existing_args)
                for arg in args:
                    if arg not in all_args:
                        all_args.append(arg)
                new_del = f"{existing_indent}del {', '.join(all_args)}  # Unused\n"
                if dry_run:
                    print(f"  {filepath}:{body_start + 1}: Update del -> {', '.join(all_args)}")
                else:
                    lines[body_start] = new_del
                modified_count += 1
        else:
            # Insert new del statement
            del_stmt = f"{indent}del {', '.join(args)}  # Unused\n"
            if dry_run:
                print(f"  {filepath}:{body_start + 1}: Insert del {', '.join(args)}")
            else:
                lines.insert(body_start, del_stmt)
            modified_count += 1

    if not dry_run and modified_count > 0:
        with open(filepath, 'w') as f:
            f.writelines(lines)

    return modified_count


def main():
    parser = argparse.ArgumentParser(
        description='Add del statements for unused method arguments'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show changes without modifying files'
    )
    args = parser.parse_args()

    print("Finding ARG002/ARG003 violations...")
    violations = get_violations()

    if not violations:
        print("No violations found!")
        return

    print(f"Found {len(violations)} unused argument violations")

    # Group by file
    by_file = defaultdict(list)
    for v in violations:
        by_file[v['filename']].append(v)

    print(f"Across {len(by_file)} files\n")

    total_modified = 0
    for filepath, file_violations in sorted(by_file.items()):
        modified = process_file(filepath, file_violations, args.dry_run)
        if modified > 0:
            total_modified += modified
            if not args.dry_run:
                short_path = filepath.replace('/home/user/skypilot/', '')
                print(f"  Modified {modified} methods in {short_path}")

    action = "Would modify" if args.dry_run else "Modified"
    print(f"\n{action} {total_modified} methods total")


if __name__ == '__main__':
    main()
