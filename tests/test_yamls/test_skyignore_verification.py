#!/usr/bin/env python3

import os
import sys


def list_all_files(dir_path):
    """List all files recursively in directory, returning relative paths."""
    result = []
    for root, dirs, files in os.walk(dir_path):
        rel_path = os.path.relpath(root, dir_path)
        if rel_path == '.':
            rel_path = ''
        else:
            rel_path += '/'

        for file in files:
            result.append(rel_path + file)
    return sorted(result)


if __name__ == '__main__':
    workdir = os.path.expanduser(sys.argv[1])
    print(f'Workdir: {workdir}')

    # These files should be present (not excluded)
    expected_files = [
        'script.py',
        'data.txt',
        'keep_dir/keep.txt',
        'nested/keep_subdir/test.py',
    ]

    # These files should be excluded
    excluded_files = [
        'exclude.py',
        'temp.log',
        'exclude_dir/notes.md',
        'nested/exclude_subdir/test.sh',
    ]

    all_files = list_all_files(workdir)
    print('All files:', all_files)

    # Check if expected files exist
    missing = [f for f in expected_files if f not in all_files]
    if missing:
        print(f'ERROR: Expected files missing: {missing}')
        sys.exit(1)

    # Check if excluded files don't exist
    present = [f for f in excluded_files if f in all_files]
    if present:
        print(f'ERROR: Excluded files present: {present}')
        sys.exit(1)

    print('SUCCESS: All expected files present and all excluded files absent')
    sys.exit(0)
