"""Utility functions for the storage module."""
import os
import pathlib
import re
import shlex
import stat
import subprocess
from typing import Any, Dict, List, Optional, Set, TextIO, Union
import warnings
import zipfile

import colorama

from sky import exceptions
from sky import sky_logging
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import log_utils

logger = sky_logging.init_logger(__name__)

_USE_SKYIGNORE_HINT = (
    'To avoid using .gitignore, you can create a .skyignore file instead.')


def format_storage_table(storages: List[Dict[str, Any]],
                         show_all: bool = False) -> str:
    """Format the storage table for display.

    Args:
        storage_table (dict): The storage table.

    Returns:
        str: The formatted storage table.
    """
    storage_table = log_utils.create_table([
        'NAME',
        'UPDATED',
        'STORE',
        'COMMAND',
        'STATUS',
    ])

    for row in storages:
        launched_at = row['launched_at']
        if show_all:
            command = row['last_use']
        else:
            command = common_utils.truncate_long_string(
                row['last_use'], constants.LAST_USE_TRUNC_LENGTH)
        storage_table.add_row([
            # NAME
            row['name'],
            # LAUNCHED
            log_utils.readable_time_duration(launched_at),
            # CLOUDS
            ', '.join([s.value for s in row['store']]),
            # COMMAND,
            command,
            # STATUS
            row['status'].value,
        ])
    if storages:
        return str(storage_table)
    else:
        return 'No existing storage.'


def get_excluded_files_from_skyignore(src_dir_path: str) -> List[str]:
    """Generate exclude patterns from .skyignore file for both zip and AWS S3.

    Pattern rules:
    - /foo     → matches only at root (becomes ./foo)
    - foo      → matches at any depth (becomes foo and **/foo)
    - *.py     → matches at any depth
    - dir/*.b  → matches only in that specific path
    """
    skyignore_path = os.path.join(os.path.expanduser(src_dir_path),
                                  '.skyignore')
    patterns: Set[str] = set()

    try:
        with open(skyignore_path, mode='r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                # Handle **/ prefixed patterns
                if line.startswith('**/'):
                    patterns.add(line)
                    patterns.add(line[3:])  # Add root-level match
                    # Handle recursive directory patterns like **/dir/**
                    if line.endswith('/**'):
                        base = line[3:-3]
                        patterns.add(base)
                        patterns.add(f'{base}/**')
                    continue

                # Check if pattern is anchored to root
                if line.startswith('/'):
                    pattern = line[1:]
                    patterns.add(f'./{pattern}')
                    # Add directory wildcard for non-glob patterns
                    if not any(c in pattern for c in '*?['):
                        patterns.add(f'./{pattern}/**')
                else:
                    # Unanchored patterns match at any depth
                    patterns.add(line)
                    patterns.add(f'**/{line}')
                    # Add directory wildcards for non-glob patterns
                    if not any(c in line for c in '*?['):
                        patterns.add(f'{line}/**')
                        patterns.add(f'**/{line}/**')

    except OSError:
        pass  # No .skyignore file

    return sorted(patterns)


def get_excluded_files_from_gitignore(src_dir_path: str) -> List[str]:
    """ Lists files and patterns ignored by git in the source directory

    Runs `git ls-files --ignored ...` which returns a list of excluded files and
    patterns read from .gitignore and .git/info/exclude using git.
    This will also be run for all submodules under the src_dir_path.

    Returns:
        List[str] containing files and folders to be ignored. There won't be any
        patterns.
    """
    expand_src_dir_path = os.path.expanduser(src_dir_path)

    # We will use `git ls-files` to list files that we should ignore, but
    # `ls-files` will not recurse into subdirectories. So, we need to manually
    # list the submodules and run `ls-files` within the root and each submodule.
    # Print the submodule paths relative to expand_src_dir_path, separated by
    # null chars.
    submodules_cmd = (f'git -C {shlex.quote(expand_src_dir_path)} '
                      'submodule foreach -q "printf \\$displaypath\\\\\\0"')

    try:
        submodules_output = subprocess.run(submodules_cmd,
                                           shell=True,
                                           stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE,
                                           check=True,
                                           text=True)
    except subprocess.CalledProcessError as e:
        gitignore_path = os.path.join(expand_src_dir_path,
                                      constants.GIT_IGNORE_FILE)

        if (e.returncode == exceptions.GIT_FATAL_EXIT_CODE and
                'not a git repository' in e.stderr):
            # If git failed because we aren't in a git repository, but there is
            # a .gitignore, warn the user that it will be ignored.
            if os.path.exists(gitignore_path):
                logger.warning('Detected a .gitignore file, but '
                               f'{src_dir_path} is not a git repository. The '
                               '.gitignore file will be ignored. '
                               f'{_USE_SKYIGNORE_HINT}')
            # Otherwise, this is fine and we can exit early.
            return []

        if e.returncode == exceptions.COMMAND_NOT_FOUND_EXIT_CODE:
            # Git is not installed. This is fine, skip the check.
            # If .gitignore is present, warn the user.
            if os.path.exists(gitignore_path):
                logger.warning(f'Detected a .gitignore file in {src_dir_path}, '
                               'but git is not installed. The .gitignore file '
                               f'will be ignored. {_USE_SKYIGNORE_HINT}')
            return []

        # Pretty much any other error is unexpected, so re-raise.
        raise

    # submodules_output will contain each submodule path (relative to
    # src_dir_path), each ending with a null character.
    # .split will have an empty string at the end because of the final null
    # char, so trim it.
    submodules = submodules_output.stdout.split('\0')[:-1]

    # The empty string is the relative reference to the src_dir_path.
    all_git_repos = [''] + [
        # We only care about submodules that are a subdirectory of src_dir_path.
        submodule for submodule in submodules if not submodule.startswith('../')
    ]

    excluded_list: List[str] = []
    for repo in all_git_repos:
        # repo is the path relative to src_dir_path. Get the full path.
        repo_path = os.path.join(expand_src_dir_path, repo)
        # This command outputs a list to be excluded according to .gitignore,
        # .git/info/exclude, and global exclude config.
        # -z: filenames terminated by \0 instead of \n
        # --others: show untracked files
        # --ignore: out of untracked files, only show ignored files
        # --exclude-standard: use standard exclude rules (required for --ignore)
        # --directory: if an entire directory is ignored, collapse to a single
        #              entry rather than listing every single file
        # Since we are using --others instead of --cached, this will not show
        # files that are tracked but also present in .gitignore.
        filter_cmd = (f'git -C {shlex.quote(repo_path)} ls-files -z '
                      '--others --ignore --exclude-standard --directory')
        output = subprocess.run(filter_cmd,
                                shell=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                check=True,
                                text=True)
        # Don't catch any errors. We would only expect to see errors during the
        # first git invocation - so if we see any here, crash.

        output_list = output.stdout.split('\0')
        # trim the empty string at the end
        output_list = output_list[:-1]

        for item in output_list:

            if repo == '' and item == './':
                logger.warning(f'{src_dir_path} is within a git repo, but the '
                               'entire directory is ignored by git. We will '
                               'ignore all git exclusions. '
                               f'{_USE_SKYIGNORE_HINT}')
                return []

            to_be_excluded = os.path.join(repo, item)

            excluded_list.append(to_be_excluded)

    return excluded_list


def get_excluded_files(src_dir_path: str) -> List[str]:
    # TODO: this could return a huge list of files,
    # should think of ways to optimize.
    """List files and directories to be excluded.

    Args:
        src_dir_path (str): The path to the source directory.

    Returns:
        A list of relative paths to files and directories to be excluded from
        the source directory.
    """
    expand_src_dir_path = os.path.expanduser(src_dir_path)
    skyignore_path = os.path.join(expand_src_dir_path,
                                  constants.SKY_IGNORE_FILE)
    # Fail fast if the source is a file.
    if not os.path.exists(expand_src_dir_path):
        raise ValueError(f'{src_dir_path} does not exist.')
    if os.path.isfile(expand_src_dir_path):
        raise ValueError(f'{src_dir_path} is a file, not a directory.')
    if os.path.exists(skyignore_path):
        logger.debug(f'  {colorama.Style.DIM}'
                     f'Excluded files to sync to cluster based on '
                     f'{constants.SKY_IGNORE_FILE}.'
                     f'{colorama.Style.RESET_ALL}')
        excluded_paths = get_excluded_files_from_skyignore(src_dir_path)
    else:
        logger.debug(f'  {colorama.Style.DIM}'
                     f'Excluded files to sync to cluster based on '
                     f'{constants.GIT_IGNORE_FILE}.'
                     f'{colorama.Style.RESET_ALL}')
        excluded_paths = get_excluded_files_from_gitignore(src_dir_path)
    return excluded_paths


def is_path_excluded(abs_path: str, base_path: str,
                     patterns: List[str]) -> bool:
    """Check if a path matches any exclusion pattern.

    Pattern matching rules (similar to .gitignore):
    - `*` matches any characters except `/` (within a single path segment)
    - `**` matches zero or more path segments
    - `?` matches any single character except `/`
    - `/` at the beginning anchors the pattern to the root
    - `/` at the end matches only directories

    Examples:
    - `*.py` - matches any .py file in any directory
    - `test/*.py` - matches .py files directly in test/ directory
    - `test/**/*.py` - matches .py files anywhere under test/ directory
    - `**/test/**` - matches any path containing /test/ anywhere
    - `*/test/**` - matches paths like foo/test/bar but not test/bar or
                    a/b/test/bar
    - `build/` - matches build directory and everything under it
    - `./README.md` - matches README.md only at the root
    - `.env` - matches .env file only at the root
    - `**/.env` - matches .env files at any depth

    Args:
        abs_path: Absolute path to check
        base_path: Base directory path to calculate relative paths from
        patterns: List of exclusion patterns (from skyignore/gitignore)

    Returns:
        True if the path should be excluded, False otherwise
    """
    rel = os.path.relpath(abs_path, base_path).replace(os.path.sep, '/')

    for pattern in patterns:
        original_pattern = pattern
        pattern = pattern.rstrip('/')

        # Handle root-anchored patterns
        if pattern.startswith('/'):
            # Remove leading / and treat as root-anchored
            pattern = pattern[1:]
            path = rel
        elif pattern.startswith('./'):
            # For ./ prefixed patterns, match against ./ + relative path
            path = './' + rel
        else:
            path = rel

        # Handle trailing slash patterns (directory patterns)
        if original_pattern.endswith('/') and '**' not in pattern:
            # Pattern like 'build/' should match 'build' and 'build/anything'
            if path == pattern or path.startswith(pattern + '/'):
                return True
            continue

        # Convert glob pattern to regex more simply
        # Escape special regex chars except * and ?
        regex_pattern = re.escape(pattern)

        # Handle ** (matches zero or more path segments)
        # Leading ** should match from start (including empty)
        regex_pattern = regex_pattern.replace(r'\*\*/', '(?:.*/)?')
        # Trailing ** should match to end (including empty)
        regex_pattern = regex_pattern.replace(r'/\*\*', '(?:/.*)?')
        # Middle ** between slashes
        regex_pattern = regex_pattern.replace(r'/\*\*/', '(?:/.*/)?')
        # Standalone ** (entire pattern)
        regex_pattern = regex_pattern.replace(r'\*\*', '.*')

        # Handle * (matches any chars except /)
        regex_pattern = regex_pattern.replace(r'\*', '[^/]*')

        # Handle ? (matches single char except /)
        regex_pattern = regex_pattern.replace(r'\?', '[^/]')

        # Add anchors
        regex_pattern = '^' + regex_pattern + '$'

        # For non-** patterns with *, ensure path depth matches
        if '*' in pattern and '**' not in pattern and '/' in pattern:
            # Count slashes to ensure depth matches
            if pattern.count('/') != path.count('/'):
                continue

        # Compile and match
        if re.match(regex_pattern, path):
            return True

    return False


def zip_files_and_folders(items: List[str],
                          output_file: Union[str, pathlib.Path],
                          log_file: Optional[TextIO] = None):

    def _store_symlink(zipf, path: str, is_dir: bool):
        # Get the target of the symlink
        target = os.readlink(path)
        # Use relative path as absolute path will not be able to resolve on
        # remote API server.
        if os.path.isabs(target):
            target = os.path.relpath(target, os.path.dirname(path))
        # Create a ZipInfo instance
        zi = zipfile.ZipInfo(path + '/') if is_dir else zipfile.ZipInfo(path)
        # Set external attributes to mark as symlink
        zi.external_attr = 0xA1ED0000
        # Write symlink target as content
        zipf.writestr(zi, target)

    with warnings.catch_warnings():
        warnings.filterwarnings('ignore',
                                category=UserWarning,
                                message='Duplicate name:')
        with zipfile.ZipFile(output_file, 'w') as zipf:
            for item in items:
                item = os.path.expanduser(item)
                if not os.path.isfile(item) and not os.path.isdir(item):
                    raise ValueError(f'{item} does not exist.')
                if os.path.isfile(item):
                    # Add the file to the zip archive even if it matches
                    # patterns in dot ignore files, as it was explicitly
                    # specified by user.
                    zipf.write(item)
                elif os.path.isdir(item):
                    excluded_patterns = get_excluded_files(item)

                    for root, dirs, files in os.walk(item, followlinks=False):
                        # prune dirs we shouldn't enter
                        dirs[:] = [
                            d for d in dirs if not is_path_excluded(
                                os.path.join(root, d), item, excluded_patterns)
                        ]

                        # record directories
                        # (important for empty ones & symlinks)
                        for d in dirs:
                            dir_path = os.path.join(root, d)
                            if os.path.islink(dir_path):
                                _store_symlink(zipf, dir_path, is_dir=True)
                            else:
                                zipf.write(dir_path)

                        for file in files:
                            file_path = os.path.join(root, file)
                            if is_path_excluded(file_path, item,
                                                excluded_patterns):
                                continue
                            if os.path.islink(file_path):
                                _store_symlink(zipf, file_path, is_dir=False)
                                continue
                            if stat.S_ISSOCK(os.stat(file_path).st_mode):
                                continue
                            zipf.write(file_path)
                if log_file is not None:
                    log_file.write(f'Zipped {item}\n')
