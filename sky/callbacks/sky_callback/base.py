import json
import os
import threading
import time
from typing import List, Optional

import psutil

# NOTE: SKY_REMOTE_BENCHMARK_DIR must be the same as
# _SKY_REMOTE_BENCHMARK_DIR in sky/benchmark/contoller.py
SKY_REMOTE_BENCHMARK_DIR = '~/sky_benchmark_dir'
BENCHMARK_SUMMARY = 'benchmark_summary.json'


class SkyCallback:

    def __init__(self,
                 log_dir: str = SKY_REMOTE_BENCHMARK_DIR,
                 warmup_steps: int = 1):
        assert warmup_steps >= 0

        # Create a log directory.
        self.log_dir = os.path.expanduser(log_dir)
        os.makedirs(self.log_dir, exist_ok=True)

        # TODO(woosuk): Do not store the entire timestamps.
        self.step_begins = []
        self.step_ends = []

        # Create a writer thread.
        self._worker = _AsyncSummaryWriter(self.log_dir, warmup_steps,
                                           self.step_begins, self.step_ends)
        self._worker.start()

    def config(self, total_train_steps: int):
        self._worker.total_steps = total_train_steps

    def on_train_step_begin(self):
        now = time.time()
        self.step_begins.append(now)

    def on_train_step_end(self):
        now = time.time()
        self.step_ends.append(now)


class _AsyncSummaryWriter(threading.Thread):

    class _BenchmarkSummary:

        def __init__(self,
                     boot_time: float,
                     create_time: float,
                     warmup_steps: int,
                     total_steps: Optional[int] = None,
                     num_steps: int = 0,
                     train_start_time: Optional[float] = None,
                     warmup_end_time: Optional[float] = None,
                     last_time: Optional[float] = None,
                     step_time: Optional[float] = None,
                     estimated_total_time: Optional[float] = None):
            self.boot_time = boot_time
            self.create_time = create_time
            self.warmup_steps = warmup_steps
            self.total_steps = total_steps
            self.num_steps = num_steps
            self.train_start_time = train_start_time
            self.warmup_end_time = warmup_end_time
            self.last_time = last_time
            self.step_time = step_time
            self.estimated_total_time = estimated_total_time

    def __init__(self,
                 log_dir: str,
                 warmup_steps: int,
                 step_begins: List[float],
                 step_ends: List[float],
                 write_interval: float = 1):
        threading.Thread.__init__(self)
        self.daemon = True

        self.log_dir = log_dir
        self.summary = self._BenchmarkSummary(
            boot_time=psutil.boot_time(),
            create_time=psutil.Process(os.getpid()).create_time(),
            warmup_steps=warmup_steps,
        )
        self.step_begins = step_begins
        self.step_ends = step_ends
        self.write_interval = write_interval
        self.total_steps = None

    def _update_summary(self):
        summary = self.summary
        summary.total_steps = self.total_steps
        if summary.total_steps is not None:
            assert summary.warmup_steps < summary.total_steps

        num_step_begins = len(self.step_begins)
        num_steps = max(num_step_begins - 1, 0)
        summary.num_steps = num_steps

        if num_steps > 0:
            last_time = self.step_begins[num_steps]
            summary.last_time = last_time

        if summary.train_start_time is None:
            if num_step_begins > 0:
                summary.train_start_time = self.step_begins[0]
        if summary.warmup_end_time is None:
            if num_step_begins > summary.warmup_steps:
                summary.warmup_end_time = self.step_begins[summary.warmup_steps]

        if num_step_begins > summary.warmup_steps + 1:
            time_after_warmup = last_time - summary.warmup_end_time
            steps_after_warmup = num_steps - summary.warmup_steps
            step_time = time_after_warmup / steps_after_warmup
            summary.step_time = step_time

            if summary.total_steps is not None:
                # NOTE: total_time does not include the time
                # between booting and the process creation.
                time_until_warmup = summary.warmup_end_time - summary.create_time
                steps_after_warmup = summary.total_steps - summary.warmup_steps
                total_time = time_until_warmup + step_time * steps_after_warmup
                summary.estimated_total_time = total_time

    def _write_summary(self):
        with open(os.path.join(self.log_dir, BENCHMARK_SUMMARY), 'w') as f:
            json.dump(self.summary.__dict__, f)

    def run(self):
        while True:
            self._update_summary()
            self._write_summary()
            time.sleep(self.write_interval)
