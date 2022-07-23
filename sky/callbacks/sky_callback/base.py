"""Generic SkyCallback class used to build framework-specific callbacks."""
import atexit
import datetime
import json
import os
import threading
import time
from typing import List, Optional

import psutil

# NOTE: This must be the same as _SKY_REMOTE_BENCHMARK_DIR_SYMLINK
# in sky/benchmark/benchmark_utils.py.
_SKY_REMOTE_BENCHMARK_DIR = '~/sky_benchmark_dir'
# NOTE: This must be the same as _BENCHMARK_SUMMARY
# in sky/benchmark/benchmark_utils.py.
_BENCHMARK_SUMMARY = 'summary.json'


class BaseCallback:

    def __init__(self,
                 log_dir: Optional[str] = None,
                 total_steps: Optional[int] = None,
                 warmup_steps: int = 1) -> None:
        if total_steps is not None:
            if total_steps < 0:
                raise ValueError('total_steps should be non-negative.')
            if total_steps < warmup_steps:
                raise ValueError('total_steps should be greater than or '
                                 'equal to warmup_steps.')
        if warmup_steps < 0:
            raise ValueError('warmup_steps should be non-negative.')

        # Create a log directory.
        if log_dir is None:
            log_dir = _SKY_REMOTE_BENCHMARK_DIR
        log_dir = os.path.join(
            log_dir, 'sky-callback-' +
            datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S-%f'))
        log_dir = os.path.expanduser(log_dir)
        os.makedirs(log_dir, exist_ok=True)

        # TODO(woosuk): Do not store the entire timestamps.
        self._step_begins = []
        self._step_ends = []

        # Create a writer thread.
        self._worker = _AsyncSummaryWriter(log_dir, total_steps, warmup_steps,
                                           self._step_begins, self._step_ends)
        self._worker.start()

        # The writer thread is a daemon thread, which is automatically killed
        # when the main process exits. The problem is, the training process
        # (and the writer daemon) can exit before the logs of the last few steps
        # are saved, because there is at most 1 sec (= save interval) time lag.
        # The purpose of this exit handler is to block the main process until
        # the daemon saves the up-to-date log and gracefully terminates.
        # Refer to: https://superfastpython.com/stop-daemon-thread/
        atexit.register(self._worker.stop)

    def on_step_begin(self) -> None:
        # Do not acuqire a lock for the sake of performance.
        now = time.time()
        self._step_begins.append(now)

    def on_step_end(self) -> None:
        # Do not acuqire a lock for the sake of performance.
        now = time.time()
        self._step_ends.append(now)


class _AsyncSummaryWriter(threading.Thread):

    class _BenchmarkSummary:

        def __init__(self,
                     boot_time: float,
                     create_time: float,
                     total_steps: Optional[int],
                     warmup_steps: int,
                     num_steps: int = 0,
                     first_step_time: Optional[float] = None,
                     warmup_end_time: Optional[float] = None,
                     last_step_time: Optional[float] = None,
                     time_per_step: Optional[float] = None,
                     estimated_total_time: Optional[float] = None) -> None:
            self.boot_time = boot_time
            self.create_time = create_time
            self.warmup_steps = warmup_steps
            self.total_steps = total_steps
            self.num_steps = num_steps
            self.first_step_time = first_step_time
            self.warmup_end_time = warmup_end_time
            self.last_step_time = last_step_time
            self.time_per_step = time_per_step
            self.estimated_total_time = estimated_total_time

    def __init__(self,
                 log_dir: str,
                 total_steps: Optional[int],
                 warmup_steps: int,
                 step_begins: List[float],
                 step_ends: List[float],
                 write_interval_seconds: float = 5) -> None:
        threading.Thread.__init__(self, daemon=True)
        self._log_path = os.path.join(log_dir, _BENCHMARK_SUMMARY)
        self._summary = self._BenchmarkSummary(
            boot_time=psutil.boot_time(),
            create_time=psutil.Process(os.getpid()).create_time(),
            total_steps=total_steps,
            warmup_steps=warmup_steps,
        )
        self._step_begins = step_begins
        self._step_ends = step_ends
        self._write_interval_seconds = write_interval_seconds

        # The thread will receive a stop signal when the main process exits,
        # so that it can save the up-to-date summary before termination.
        self._received_stop_signal = threading.Event()

    def stop(self) -> None:
        self._received_stop_signal.set()
        self.join()

    def _update_summary(self) -> None:
        summary = self._summary
        num_step_begins = len(self._step_begins)
        num_step_ends = len(self._step_ends)

        if summary.first_step_time is None:
            if num_step_begins > 0:
                summary.first_step_time = self._step_begins[0]
        if summary.warmup_end_time is None:
            if num_step_ends >= summary.warmup_steps:
                summary.warmup_end_time = self._step_ends[summary.warmup_steps -
                                                          1]

        num_steps = num_step_ends
        summary.num_steps = num_steps
        if num_steps > 0:
            last_step_time = self._step_ends[num_steps - 1]
            summary.last_step_time = last_step_time

        if num_steps > summary.warmup_steps:
            time_after_warmup = last_step_time - summary.warmup_end_time
            steps_after_warmup = num_steps - summary.warmup_steps
            time_per_step = time_after_warmup / steps_after_warmup
            summary.time_per_step = time_per_step

            if summary.total_steps is not None:
                # NOTE: total_time does not include the time
                # between booting and the process creation.
                time_until_warmup = summary.warmup_end_time - summary.create_time
                steps_after_warmup = summary.total_steps - summary.warmup_steps
                total_time = time_until_warmup + time_per_step * steps_after_warmup
                summary.estimated_total_time = total_time

    def _write_summary(self) -> None:
        with open(self._log_path, 'w') as f:
            json.dump(self._summary.__dict__, f)

    def run(self) -> None:
        """Periodically updates and saves the summary."""
        next_write_time = 0
        while not self._received_stop_signal.is_set():
            now = time.time()
            if now >= next_write_time:
                self._update_summary()
                self._write_summary()
                next_write_time = now + self._write_interval_seconds
            else:
                # Sleep for at most 1 second to avoid busy loop.
                time.sleep(min(1, next_write_time - now))

        # Update and save the summary one last time.
        self._update_summary()
        self._write_summary()
