import json
import os
import threading
import time
from typing import List, Optional

import psutil

# This must be the same as _SKY_REMOTE_BENCHMARK_DIR_SYMLINK
# in sky/benchmark/benchmark_utils.py.
_SKY_REMOTE_BENCHMARK_DIR = '~/sky_benchmark_dir'

# This must be the same as _BENCHMARK_SUMMARY
# in sky/benchmark/benchmark_utils.py.
_BENCHMARK_SUMMARY = 'benchmark_summary.json'


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
            self.log_dir = _SKY_REMOTE_BENCHMARK_DIR
        else:
            self.log_dir = log_dir
        self.log_dir = os.path.expanduser(self.log_dir)
        os.makedirs(self.log_dir, exist_ok=True)

        # TODO(woosuk): Do not store the entire timestamps.
        self._step_begins = []
        self._step_ends = []

        # Create a writer thread.
        self._worker = _AsyncSummaryWriter(self.log_dir, total_steps,
                                           warmup_steps, self._step_begins,
                                           self._step_ends)
        self._worker.start()

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
                 write_interval: float = 1) -> None:
        threading.Thread.__init__(self)
        self.daemon = True

        self.log_dir = log_dir
        self.summary = self._BenchmarkSummary(
            boot_time=psutil.boot_time(),
            create_time=psutil.Process(os.getpid()).create_time(),
            total_steps=total_steps,
            warmup_steps=warmup_steps,
        )
        self.step_begins = step_begins
        self.step_ends = step_ends
        self.write_interval = write_interval

    def _update_summary(self) -> None:
        summary = self.summary
        num_step_begins = len(self.step_begins)
        num_steps = max(num_step_begins - 1, 0)
        summary.num_steps = num_steps

        if num_steps > 0:
            last_step_time = self.step_begins[num_steps]
            summary.last_step_time = last_step_time

        if summary.first_step_time is None:
            if num_step_begins > 0:
                summary.first_step_time = self.step_begins[0]
        if summary.warmup_end_time is None:
            if num_step_begins > summary.warmup_steps:
                summary.warmup_end_time = self.step_begins[summary.warmup_steps]

        if num_step_begins > summary.warmup_steps + 1:
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
        with open(os.path.join(self.log_dir, _BENCHMARK_SUMMARY), 'w') as f:
            json.dump(self.summary.__dict__, f)

    def run(self) -> None:
        next_write_time = 0
        while True:
            now = time.time()
            if now >= next_write_time:
                self._update_summary()
                self._write_summary()
                next_write_time = now + self.write_interval
            else:
                time.sleep(next_write_time - now)
