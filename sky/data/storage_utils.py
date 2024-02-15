"""Utility functions for the storage module."""
import os
import shlex
import subprocess
from typing import Any, Dict, List

import colorama

from sky import exceptions
from sky import sky_logging
from sky.utils import log_utils
from sky.utils.cli_utils import status_utils

logger = sky_logging.init_logger(__name__)

_FILE_EXCLUSION_FROM_GITIGNORE_FAILURE_MSG = (
    f'{colorama.Fore.YELLOW}Warning: Files/dirs '
    'specified in .gitignore will be uploaded '
    'to the cloud storage for {path!r}'
    'due to the following error: {error_msg!r}')


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
            command = status_utils.truncate_long_string(
                row['last_use'], status_utils.COMMAND_TRUNC_LENGTH)
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


def get_excluded_files_from_gitignore(src_dir_path: str) -> List[str]:
    """ Lists files and patterns ignored by git in the source directory

    Runs `git status --ignored` which returns a list of excluded files and
    patterns read from .gitignore and .git/info/exclude using git.
    `git init` is run if SRC_DIR_PATH is not a git repository and removed
    after obtaining excluded list.

    Returns:
        List[str] containing files and patterns to be ignored.  Some of the
        patterns include, **/mydir/*.txt, !myfile.log, or file-*/.
    """
    expand_src_dir_path = os.path.expanduser(src_dir_path)

    git_exclude_path = os.path.join(expand_src_dir_path, '.git/info/exclude')
    gitignore_path = os.path.join(expand_src_dir_path, '.gitignore')

    git_exclude_exists = os.path.isfile(git_exclude_path)
    gitignore_exists = os.path.isfile(gitignore_path)

    # This command outputs a list to be excluded according to .gitignore
    # and .git/info/exclude
    filter_cmd = (f'git -C {shlex.quote(expand_src_dir_path)} '
                  'status --ignored --porcelain=v1')
    excluded_list: List[str] = []

    if git_exclude_exists or gitignore_exists:
        try:
            output = subprocess.run(filter_cmd,
                                    shell=True,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    check=True,
                                    text=True)
        except subprocess.CalledProcessError as e:
            # when the SRC_DIR_PATH is not a git repo and .git
            # does not exist in it
            if e.returncode == exceptions.GIT_FATAL_EXIT_CODE:
                if 'not a git repository' in e.stderr:
                    # Check if the user has 'write' permission to
                    # SRC_DIR_PATH
                    if not os.access(expand_src_dir_path, os.W_OK):
                        error_msg = 'Write permission denial'
                        logger.warning(
                            _FILE_EXCLUSION_FROM_GITIGNORE_FAILURE_MSG.format(
                                path=src_dir_path, error_msg=error_msg))
                        return excluded_list
                    init_cmd = f'git -C {expand_src_dir_path} init'
                    try:
                        subprocess.run(init_cmd,
                                       shell=True,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       check=True)
                        output = subprocess.run(filter_cmd,
                                                shell=True,
                                                stdout=subprocess.PIPE,
                                                stderr=subprocess.PIPE,
                                                check=True,
                                                text=True)
                    except subprocess.CalledProcessError as init_e:
                        logger.warning(
                            _FILE_EXCLUSION_FROM_GITIGNORE_FAILURE_MSG.format(
                                path=src_dir_path, error_msg=init_e.stderr))
                        return excluded_list
                    if git_exclude_exists:
                        # removes all the files/dirs created with 'git init'
                        # under .git/ except .git/info/exclude
                        remove_files_cmd = (f'find {expand_src_dir_path}' \
                                            f'/.git -path {git_exclude_path}' \
                                            ' -prune -o -type f -exec rm -f ' \
                                            '{} +')
                        remove_dirs_cmd = (f'find {expand_src_dir_path}' \
                                        f'/.git -path {git_exclude_path}' \
                                        ' -o -type d -empty -delete')
                        subprocess.run(remove_files_cmd,
                                       shell=True,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       check=True)
                        subprocess.run(remove_dirs_cmd,
                                       shell=True,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       check=True)

        output_list = output.stdout.split('\n')
        for line in output_list:
            # FILTER_CMD outputs items preceded by '!!'
            # to specify excluded files/dirs
            # e.g., '!! mydir/' or '!! mydir/myfile.txt'
            if line.startswith('!!'):
                to_be_excluded = line[3:]
                if line.endswith('/'):
                    # aws s3 sync and gsutil rsync require * to exclude
                    # files/dirs under the specified directory.
                    to_be_excluded += '*'
                excluded_list.append(to_be_excluded)
    return excluded_list
