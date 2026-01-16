#!/usr/bin/env python3
"""Convert noqa human-readable names back to codes.

Ruff doesn't consistently support human-readable names in noqa comments.
This script converts them back to codes for reliability.

Usage:
    python scripts/convert_noqa_to_codes.py
"""

import re
import subprocess
from pathlib import Path

# Mapping of human-readable names to codes
NAME_TO_CODE = {
    # A - flake8-builtins
    'builtin-variable-shadowing': 'A001',
    'builtin-argument-shadowing': 'A002',
    'builtin-attribute-shadowing': 'A003',
    'builtin-import-shadowing': 'A004',
    'builtin-module-shadowing': 'A005',
    'builtin-lambda-argument-shadowing': 'A006',
    # ARG - flake8-unused-arguments
    'unused-function-argument': 'ARG001',
    'unused-method-argument': 'ARG002',
    'unused-class-method-argument': 'ARG003',
    'unused-static-method-argument': 'ARG004',
    'unused-lambda-argument': 'ARG005',
    # B - flake8-bugbear
    'assignment-to-os-environ': 'B003',
    'unused-loop-control-variable': 'B007',
    'function-call-in-default-argument': 'B008',
    'assert-false': 'B011',
    'function-uses-loop-variable': 'B023',
    'empty-method-without-abstract-decorator': 'B027',
    're-sub-positional-args': 'B034',
    'raise-without-from-inside-except': 'B904',
    # BLE - flake8-blind-except
    'blind-except': 'BLE001',
    # C4 - flake8-comprehensions
    'unnecessary-generator-set': 'C401',
    'unnecessary-list-comprehension-set': 'C403',
    'unnecessary-collection-call': 'C408',
    'unnecessary-double-cast-or-process': 'C414',
    'unnecessary-comprehension': 'C416',
    'unnecessary-map': 'C417',
    'unnecessary-comprehension-in-call': 'C419',
    # E - pycodestyle errors
    'mixed-spaces-and-tabs': 'E101',
    'module-import-not-at-top-of-file': 'E402',
    'line-too-long': 'E501',
    'type-comparison': 'E721',
    'lambda-assignment': 'E731',
    # F - Pyflakes
    'unused-import': 'F401',
    'undefined-local-with-import-star': 'F403',
    'undefined-local-with-import-star-usage': 'F405',
    'undefined-name': 'F821',
    'unused-variable': 'F841',
    # I - isort
    'unsorted-imports': 'I001',
    # PLC - pylint convention
    'import-outside-toplevel': 'PLC0415',
    # Q - flake8-quotes
    'bad-quotes-inline-string': 'Q000',
    'avoidable-escaped-quote': 'Q003',
    # SLF - flake8-self
    'private-member-access': 'SLF001',
    # W - pycodestyle warnings
    'trailing-whitespace': 'W291',
    'invalid-escape-sequence': 'W605',
}


def convert_noqa_in_file(filepath: Path) -> int:
    """Convert noqa names to codes in a file."""
    with open(filepath, 'r') as f:
        content = f.read()

    # Pattern to match noqa comments
    noqa_pattern = re.compile(r'# noqa: ([^\n]+)')

    def replace_names(match):
        codes_str = match.group(1)
        parts = [p.strip() for p in codes_str.split(',')]
        new_parts = []
        for part in parts:
            # Check if it's a human-readable name
            if part in NAME_TO_CODE:
                new_parts.append(NAME_TO_CODE[part])
            else:
                new_parts.append(part)
        return '# noqa: ' + ', '.join(new_parts)

    new_content, count = noqa_pattern.subn(replace_names, content)

    if new_content != content:
        with open(filepath, 'w') as f:
            f.write(new_content)
        return count

    return 0


def main():
    # Find all Python files in sky/
    result = subprocess.run(
        ['find', 'sky/', '-name', '*.py', '-type', 'f'],
        capture_output=True,
        text=True
    )
    files = result.stdout.strip().split('\n')

    total_converted = 0
    files_modified = 0

    for filepath in files:
        if not filepath:
            continue
        path = Path(filepath)
        if not path.exists():
            continue

        count = convert_noqa_in_file(path)
        if count > 0:
            total_converted += count
            files_modified += 1

    print(f'Converted noqa comments in {files_modified} files')


if __name__ == '__main__':
    main()
