"""Cancel signal files of managed jobs.

`sky jobs cancel` tells the controller process that runs a job to cancel it by
writing a file named after the job id into the signal directory
(``CONSOLIDATED_SIGNAL_PATH``). Every controller process polls that directory
and takes the signals of the jobs it runs. An empty file is a plain cancel;
see ``sky.jobs.utils.parse_job_cancel_file`` for the other contents.

When the API server has several replicas, the directory is on a volume they
share, which is usually a network filesystem: a call on it can take
seconds, and listing it costs more the more entries it has. So nothing here
may run on a controller's event loop, and no file may be left behind once its
signal was taken.
"""
import os
import time
from typing import List, Optional, Set
import uuid

import filelock

from sky import sky_logging
from sky.jobs import constants as managed_job_constants
from sky.jobs import state as managed_job_state

logger = sky_logging.init_logger(__name__)

# Until this module existed, writers wrote the signal in place while holding
# a file lock named '<job id>.lock', and nobody removed the lock file.
_LEGACY_LOCK_SUFFIX = '.lock'
# A signal is written under this kind of name first and then renamed.
_TEMPORARY_SUFFIX = '.tmp'

# A temporary file is renamed moments after it is created. One that is this
# old was left by a writer that died.
_TEMPORARY_FILE_MAX_AGE_SECONDS = 3600
_GC_INTERVAL_SECONDS = 3600


def _signal_path(job_id: int) -> str:
    return os.path.join(managed_job_constants.CONSOLIDATED_SIGNAL_PATH,
                        str(job_id))


def _remove_if_exists(path: str) -> bool:
    try:
        os.remove(path)
    except FileNotFoundError:
        return False
    return True


def write(job_id: int, content: str = '') -> None:
    """Writes the cancel signal of a job, replacing an earlier one.

    The signal appears under its name with its whole content at once, so a
    controller never reads a half-written signal and no lock is needed.

    Raises:
        OSError: the signal could not be written.
    """
    signal_path = _signal_path(job_id)
    # Neither all digits nor a legacy lock name, so no controller takes it
    # for a signal.
    temporary_path = f'{signal_path}.{uuid.uuid4().hex}{_TEMPORARY_SUFFIX}'
    try:
        with open(temporary_path, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(temporary_path, signal_path)
    except OSError:
        _remove_if_exists(temporary_path)
        raise


def exists(job_id: int) -> bool:
    """Whether a job has a cancel signal that was not taken yet."""
    return os.path.exists(_signal_path(job_id))


def _read_and_remove(signal_path: str) -> Optional[str]:
    try:
        with open(signal_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
    except FileNotFoundError:
        return None
    except Exception as e:  # pylint: disable=broad-except
        # An unreadable signal is still a request to cancel.
        logger.debug(f'Problem occurred when reading {signal_path}: {e}')
        content = ''
    _remove_if_exists(signal_path)
    return content


def take(job_id: int) -> Optional[str]:
    """Reads and removes the cancel signal of a job.

    Returns:
        The content of the signal ('' for a plain cancel), or None if the job
        has no signal.
    """
    signal_path = _signal_path(job_id)
    lock_path = signal_path + _LEGACY_LOCK_SUFFIX
    if not os.path.exists(lock_path):
        return _read_and_remove(signal_path)
    # A writer older than write() may be writing this signal in place right
    # now, during a rolling update of the API server. It holds this lock while
    # it does. Wait for it, so that the signal is read whole.
    # TODO(cooperc): Remove the lock handling once no supported API server
    # writes signals under a lock.
    with filelock.FileLock(lock_path):
        content = _read_and_remove(signal_path)
    _remove_if_exists(lock_path)
    return content


def remove(job_id: int) -> None:
    """Removes the cancel signal of a job without reading it."""
    signal_path = _signal_path(job_id)
    _remove_if_exists(signal_path)
    _remove_if_exists(signal_path + _LEGACY_LOCK_SUFFIX)


def remove_stale_files() -> int:
    """Removes the files in the signal directory that nobody will use again.

    These are:
      - The signal of a job that is over. This happens when a job ends by
        itself just as it is cancelled: its controller process has let go of
        it and never looks at the signal.
      - The legacy lock file of a job that is over. A signal is only written
        for an unfinished job, so nobody takes that lock again.
      - A temporary file of a writer that died before renaming it.

    Which jobs are over is asked from the database once. The files are not
    looked at one by one: on a network filesystem that takes far longer than
    removing them, when older code has left tens of thousands of lock files.

    Files that are not named after a job id are left alone: other components
    keep their own files in this directory.

    Returns:
        The number of files removed.
    """
    directory = managed_job_constants.CONSOLIDATED_SIGNAL_PATH
    try:
        names = os.listdir(directory)
    except FileNotFoundError:
        return 0
    signal_job_ids: Set[int] = set()
    lock_job_ids: Set[int] = set()
    temporary_names: List[str] = []
    for name in names:
        if name.isdigit():
            signal_job_ids.add(int(name))
        elif name.endswith(_LEGACY_LOCK_SUFFIX):
            job_id_part = name[:-len(_LEGACY_LOCK_SUFFIX)]
            if job_id_part.isdigit():
                lock_job_ids.add(int(job_id_part))
        elif (name.endswith(_TEMPORARY_SUFFIX) and
              name.split('.', 1)[0].isdigit()):
            temporary_names.append(name)
    removed = 0
    if signal_job_ids or lock_job_ids:
        # Read after the directory was listed: a job that had a file then
        # and is not unfinished now is over.
        unfinished_job_ids = set(
            managed_job_state.get_nonterminal_job_ids_by_name(None,
                                                              all_users=True))
        for job_id in sorted(signal_job_ids - unfinished_job_ids):
            removed += int(_remove_if_exists(_signal_path(job_id)))
        for job_id in sorted(lock_job_ids - unfinished_job_ids):
            removed += int(
                _remove_if_exists(_signal_path(job_id) + _LEGACY_LOCK_SUFFIX))
    oldest_kept = time.time() - _TEMPORARY_FILE_MAX_AGE_SECONDS
    for name in temporary_names:
        path = os.path.join(directory, name)
        try:
            is_old = os.stat(path).st_mtime < oldest_kept
        except FileNotFoundError:
            continue
        if is_old:
            removed += int(_remove_if_exists(path))
    return removed


def gc_stale_files() -> None:
    """Removes stale files from the signal directory, forever.

    Runs in the thread of the one controller process that was elected to
    garbage collect (see sky.jobs.log_gc).
    """
    while True:
        try:
            removed = remove_stale_files()
            if removed:
                logger.info(f'Removed {removed} stale files from the cancel '
                            'signal directory.')
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'Error removing stale cancel signal files: {e}',
                         exc_info=True)
        time.sleep(_GC_INTERVAL_SECONDS)
