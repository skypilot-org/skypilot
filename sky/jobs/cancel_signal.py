"""Cancel signal files of managed jobs.

`sky jobs cancel` tells the controller process that runs a job to cancel it by
writing a file named after the job id into the signal directory
(``CONSOLIDATED_SIGNAL_PATH``). Every controller process polls that directory
and takes the signals of the jobs it runs. An empty file is a plain cancel;
see ``sky.jobs.utils.parse_job_cancel_file`` for the other contents.

When the API server has several replicas, the directory is on a volume they
share, which is usually a network filesystem: a call on it can take
seconds, and listing it costs more the more entries it has. So a controller
makes these calls off its event loop (see in_thread), and no file is left
behind once its signal was taken.

No lock is involved. A writer puts a whole signal in place with one rename,
and a controller takes a signal away with one rename. A signal is therefore
either taken with its whole content or still in place, also when a second
cancel arrives while the first is being taken.
"""
import asyncio
import concurrent.futures
import os
import time
from typing import Any, Callable, List, Optional, Set, TypeVar
import uuid

import filelock

from sky import sky_logging
from sky.jobs import constants as managed_job_constants
from sky.jobs import state as managed_job_state
from sky.utils import context_utils

logger = sky_logging.init_logger(__name__)

T = TypeVar('T')

# Until this module existed, writers wrote the signal in place while holding
# a file lock named '<job id>.lock', and nobody removed the lock file.
_LEGACY_LOCK_SUFFIX = '.lock'
# A signal is written under this kind of name first and then renamed.
_TEMPORARY_SUFFIX = '.tmp'

# A temporary file is renamed moments after it is created. One that is this
# old was left by a writer that died.
_TEMPORARY_FILE_MAX_AGE_SECONDS = 3600
_GC_INTERVAL_SECONDS = 3600

# A controller's event loop makes its calls on the signal directory in these
# threads (see in_thread). Deliberately NOT asyncio.to_thread's default
# executor: that one is small and shared by every blocking call of every job
# in the process. A cancel must still be noticed while those calls have it
# exhausted (e.g. many jobs tearing down at once), and a hung filesystem must
# not take its workers. The cancel, claim and deadline loops of a controller
# each wait for their one call, so with three threads none waits for another.
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=3, thread_name_prefix='cancel-signal')


def in_thread(func: Callable[..., T], *args: Any) -> 'asyncio.Future[T]':
    """Runs a blocking call on the signal directory off the event loop."""
    return context_utils.to_thread_with_executor(_EXECUTOR, func, *args)


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
    controller never takes a half-written signal.

    Raises:
        OSError: the signal could not be written.
    """
    signal_path = _signal_path(job_id)
    # Neither all digits nor a legacy lock name, so no controller takes it
    # for a signal.
    temporary_path = f'{signal_path}.{uuid.uuid4().hex}{_TEMPORARY_SUFFIX}'
    try:
        # A controller makes the directory when it starts. The file lock that
        # writers used to take made it too, so keep doing that.
        os.makedirs(managed_job_constants.CONSOLIDATED_SIGNAL_PATH,
                    exist_ok=True)
        with open(temporary_path, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(temporary_path, signal_path)
    except OSError:
        _remove_if_exists(temporary_path)
        raise


def exists(job_id: int) -> bool:
    """Whether a job has a cancel signal that was not taken yet."""
    return os.path.exists(_signal_path(job_id))


def take(job_id: int) -> Optional[str]:
    """Takes the cancel signal of a job away and returns its content.

    The signal is first renamed to a private name, in one step, and read
    after that. A cancel that is written at the same moment is therefore
    either the one taken, or still in place for the next poll: no signal is
    removed unread.

    Returns:
        The content of the signal ('' for a plain cancel), or None if the job
        has no signal.
    """
    signal_path = _signal_path(job_id)
    taken_path = f'{signal_path}.{uuid.uuid4().hex}{_TEMPORARY_SUFFIX}'
    try:
        os.rename(signal_path, taken_path)
    except FileNotFoundError:
        # Normally there is no signal. A network filesystem can also report
        # this for a rename that it applied, if the request was sent twice;
        # the private file is there then.
        if not os.path.exists(taken_path):
            return None
    lock_path = signal_path + _LEGACY_LOCK_SUFFIX
    if os.path.exists(lock_path):
        # A writer older than write() writes the signal in place while it
        # holds this lock, during a rolling update of the API server. It may
        # have opened the file before it was taken here. Such a writer makes
        # the lock file before it opens the signal, so once the lock is free
        # the file is whole.
        # TODO(cooperc): Remove once no supported API server writes signals
        # under a lock.
        with filelock.FileLock(lock_path):
            pass
    try:
        with open(taken_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
    except Exception as e:  # pylint: disable=broad-except
        # An unreadable signal is still a request to cancel.
        logger.debug('Problem occurred when reading the cancel signal of '
                     f'job {job_id}: {e}')
        content = ''
    _remove_if_exists(taken_path)
    return content


def remove(job_id: int) -> None:
    """Removes the cancel signal of a job without reading it."""
    _remove_if_exists(_signal_path(job_id))


def remove_stale_files() -> int:
    """Removes the files in the signal directory that nobody will use again.

    These are:
      - The signal of a job that is over. This happens when a job ends by
        itself just as it is cancelled: its controller process has let go of
        it and never looks at the signal.
      - The legacy lock file of a job that is over. A signal is only written
        for an unfinished job, so nobody takes that lock again. Nothing else
        removes a lock file: removing one that a writer still waits for
        would let two writers hold "the" lock, on two files.
      - A temporary file that a writer or a controller left behind when it
        died between its two steps.

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
