#!/usr/bin/env python3
"""Convert noqa codes to human-readable names.

Ruff supports both code-style (E501) and human-readable (line-too-long) noqa comments.
This script converts existing code-style noqa comments to human-readable format.

Usage:
    python scripts/convert_noqa_to_readable.py
"""

import re
import subprocess
from pathlib import Path

# Mapping of ruff codes to human-readable names
# Generated from: ruff rule --all --output-format json
CODE_TO_NAME = {
    # A - flake8-builtins
    'A001': 'builtin-variable-shadowing',
    'A002': 'builtin-argument-shadowing',
    'A003': 'builtin-attribute-shadowing',
    'A004': 'builtin-import-shadowing',
    'A005': 'builtin-module-shadowing',
    'A006': 'builtin-lambda-argument-shadowing',
    # ARG - flake8-unused-arguments
    'ARG001': 'unused-function-argument',
    'ARG002': 'unused-method-argument',
    'ARG003': 'unused-class-method-argument',
    'ARG004': 'unused-static-method-argument',
    'ARG005': 'unused-lambda-argument',
    # B - flake8-bugbear
    'B003': 'assignment-to-os-environ',
    'B007': 'unused-loop-control-variable',
    'B008': 'function-call-in-default-argument',
    'B011': 'assert-false',
    'B023': 'function-uses-loop-variable',
    'B027': 'empty-method-without-abstract-decorator',
    'B034': 're-sub-positional-args',
    'B904': 'raise-without-from-inside-except',
    # BLE - flake8-blind-except
    'BLE001': 'blind-except',
    # C4 - flake8-comprehensions
    'C401': 'unnecessary-generator-set',
    'C403': 'unnecessary-list-comprehension-set',
    'C408': 'unnecessary-collection-call',
    'C414': 'unnecessary-double-cast-or-process',
    'C416': 'unnecessary-comprehension',
    'C417': 'unnecessary-map',
    'C419': 'unnecessary-comprehension-in-call',
    # E - pycodestyle errors
    'E101': 'mixed-spaces-and-tabs',
    'E402': 'module-import-not-at-top-of-file',
    'E501': 'line-too-long',
    'E721': 'type-comparison',
    'E731': 'lambda-assignment',
    # F - Pyflakes
    'F401': 'unused-import',
    'F403': 'undefined-local-with-import-star',
    'F405': 'undefined-local-with-import-star-usage',
    'F821': 'undefined-name',
    'F841': 'unused-variable',
    # I - isort
    'I001': 'unsorted-imports',
    # PLC - pylint convention
    'PLC0415': 'import-outside-toplevel',
    # Q - flake8-quotes
    'Q000': 'bad-quotes-inline-string',
    'Q003': 'avoidable-escaped-quote',
    # SLF - flake8-self
    'SLF001': 'private-member-access',
    # W - pycodestyle warnings
    'W291': 'trailing-whitespace',
    'W605': 'invalid-escape-sequence',
}


def convert_noqa_in_file(filepath: Path) -> int:
    """Convert noqa codes to human-readable names in a file."""
    with open(filepath, 'r') as f:
        content = f.read()

    # Pattern to match noqa comments with codes
    # Matches: # noqa: CODE1, CODE2, ...
    noqa_pattern = re.compile(r'# noqa: ([A-Z0-9, ]+)')

    def replace_codes(match):
        codes_str = match.group(1)
        codes = [c.strip() for c in codes_str.split(',')]
        new_codes = []
        for code in codes:
            if code in CODE_TO_NAME:
                new_codes.append(CODE_TO_NAME[code])
            else:
                new_codes.append(code)
        return '# noqa: ' + ', '.join(new_codes)

    new_content, count = noqa_pattern.subn(replace_codes, content)

    if count > 0:
        with open(filepath, 'w') as f:
            f.write(new_content)

    return count


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
            print(f'  {filepath}: {count} noqa comments converted')

    print(f'\nConverted {total_converted} noqa comments in {files_modified} files')


if __name__ == '__main__':
    main()
