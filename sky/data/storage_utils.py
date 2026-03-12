"""Utility functions for the storage module."""
import enum
import glob
import os
import pathlib
import shlex
import stat
import subprocess
from typing import Iterator, List, Optional, Set, TextIO, Tuple, Union
import warnings
import zipfile

import colorama

from sky import exceptions
from sky import sky_logging
from sky.skylet import constants
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)

_USE_SKYIGNORE_HINT = (
    'To avoid using .gitignore, you can create a .skyignore file instead.')


def get_excluded_files_from_skyignore(src_dir_path: str) -> List[str]:
    """List files and patterns ignored by the .skyignore file
    in the given source directory.
    """
    excluded_list: Set[str] = set()
    expand_src_dir_path = os.path.expanduser(src_dir_path)
    skyignore_path = os.path.join(expand_src_dir_path,
                                  constants.SKY_IGNORE_FILE)

    try:
        with open(skyignore_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    # Make parsing consistent with rsync.
                    # Rsync uses '/' as current directory.
                    if line.startswith('/'):
                        line = '.' + line
                    else:
                        line = '**/' + line
                    # Find all files matching the pattern.
                    matching_files = glob.glob(os.path.join(
                        expand_src_dir_path, line),
                                               recursive=True)
                    # Process filenames to comply with cloud rsync format.
                    for i in range(len(matching_files)):
                        matching_files[i] = os.path.relpath(
                            matching_files[i], expand_src_dir_path)
                    excluded_list.update(matching_files)
    except IOError as e:
        logger.warning(f'Error reading {skyignore_path}: '
                       f'{common_utils.format_exception(e, use_bracket=True)}')

    return list(excluded_list)


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


class ZipEntryKind(enum.Enum):
    """Kind of entry yielded by _iter_zip_entries."""
    FILE = 'file'
    DIR = 'dir'
    SYMLINK = 'symlink'


def iter_zip_entries(
    items: List[str],
    relative_to_items: bool = False,
) -> Iterator[Tuple[str, str, ZipEntryKind]]:
    """Iterate over file-tree entries that would be included in a zip."""

    def _get_archive_name(file_path: str, item_path: str) -> str:
        if relative_to_items:
            return os.path.relpath(file_path, os.path.dirname(item_path))
        return file_path

    for item in items:
        item = os.path.expanduser(item)
        if not os.path.isfile(item) and not os.path.isdir(item):
            raise ValueError(f'{item} does not exist.')
        if os.path.isfile(item):
            archive_name = _get_archive_name(item, item)
            yield (archive_name, item, ZipEntryKind.FILE)
        elif os.path.isdir(item):
            archive_name = _get_archive_name(item, item)
            if os.path.islink(item):
                yield (archive_name, item, ZipEntryKind.SYMLINK)
            else:
                yield (archive_name, item, ZipEntryKind.DIR)

            excluded_files = set([
                os.path.join(item, f.rstrip('/'))
                for f in get_excluded_files(item)
            ])
            for root, dirs, files in os.walk(item, followlinks=False):
                dirs[:] = [
                    d for d in dirs
                    if os.path.join(root, d) not in excluded_files
                ]
                for dir_name in dirs:
                    dir_path = os.path.join(root, dir_name)
                    archive_name = _get_archive_name(dir_path, item)
                    if os.path.islink(dir_path):
                        yield (archive_name, dir_path, ZipEntryKind.SYMLINK)
                    else:
                        yield (archive_name, dir_path, ZipEntryKind.DIR)
                for file in files:
                    file_path = os.path.join(root, file)
                    if file_path in excluded_files:
                        continue
                    archive_name = _get_archive_name(file_path, item)
                    if os.path.islink(file_path):
                        yield (archive_name, file_path, ZipEntryKind.SYMLINK)
                        continue
                    if stat.S_ISSOCK(os.stat(file_path).st_mode):
                        continue
                    yield (archive_name, file_path, ZipEntryKind.FILE)


def zip_files_and_folders(items: List[str],
                          output_file: Union[str, pathlib.Path],
                          log_file: Optional[TextIO] = None,
                          relative_to_items: bool = False,
                          compression: int = zipfile.ZIP_DEFLATED,
                          compresslevel: Optional[int] = None):
    """Zip files and folders.

    Args:
        items: List of file/folder paths to include in the zip.
        output_file: Path to the output zip file.
        log_file: Optional file to log progress to.
        relative_to_items: If True, paths in zip are relative to items.
        compression: Compression method (default: ZIP_DEFLATED for good
            compression with universal compatibility).
        compresslevel: Compression level (1-9 for DEFLATED). None uses
            the default (6).
    """

    def _store_symlink(zipf: zipfile.ZipFile, path: str, archive_name: str):
        target = os.readlink(path)
        if os.path.isabs(target):
            target = os.path.relpath(target, os.path.dirname(path))
        is_dir = os.path.isdir(path)
        zi = zipfile.ZipInfo(archive_name +
                             '/') if is_dir else zipfile.ZipInfo(archive_name)
        zi.external_attr = 0xA1ED0000
        zipf.writestr(zi, target)

    with warnings.catch_warnings():
        warnings.filterwarnings('ignore',
                                category=UserWarning,
                                message='Duplicate name:')
        with zipfile.ZipFile(output_file,
                             'w',
                             compression=compression,
                             compresslevel=compresslevel) as zipf:
            for archive_name, file_path, kind in iter_zip_entries(
                    items, relative_to_items):
                if kind == ZipEntryKind.SYMLINK:
                    _store_symlink(zipf, file_path, archive_name)
                else:
                    zipf.write(file_path, archive_name)
    if log_file is not None:
        for item in items:
            log_file.write(f'Zipped {item}\n')
