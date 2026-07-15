"""GeneratorBase ABC + shared helpers."""
from __future__ import annotations

import abc
import json
import os
import threading
from typing import Any, Dict, List, Optional


class JsonlWriter:
    """Thread-safe JSONL append-only writer."""

    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        # Truncate any leftover file so each run is self-contained.
        open(self._path, 'w').close()

    @property
    def path(self) -> str:
        return self._path

    def write(self, record: Dict[str, Any]) -> None:
        line = json.dumps(record, separators=(',', ':'))
        with self._lock:
            with open(self._path, 'a') as f:
                f.write(line + '\n')

    @staticmethod
    def load(path: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not os.path.exists(path):
            return out
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out


class WorkerContext:
    """Shared runtime info handed to every generator."""

    def __init__(self, worker_id: int, num_workers: int, output_dir: str,
                 target_cloud: str, victim_clusters: List[str],
                 duration_s: int):
        self.worker_id = worker_id
        self.num_workers = max(1, num_workers)
        self.output_dir = output_dir
        self.target_cloud = target_cloud
        self.victim_clusters = victim_clusters
        self.duration_s = duration_s


class GeneratorBase(abc.ABC):
    """Abstract base for all load generators.

    Lifecycle:
        g = SomeGenerator(spec, ctx)
        g.start()          # returns immediately; background threads run
        g.wait(timeout)    # optional; shell generators block here
        g.stop()           # graceful drain
        summary = g.summarize()
        records = g.records()
    """

    def __init__(self, spec, ctx: WorkerContext):
        self.spec = spec
        self.ctx = ctx
        self.name: str = spec.name
        self._stop = threading.Event()
        self._jsonl_path = os.path.join(
            ctx.output_dir, f'gen_{self.name}_w{ctx.worker_id}.jsonl')
        self._writer = JsonlWriter(self._jsonl_path)

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def request_stop(self) -> None:
        self._stop.set()

    @abc.abstractmethod
    def start(self) -> None:
        ...

    @abc.abstractmethod
    def stop(self) -> None:
        ...

    def wait(self, timeout: Optional[float] = None) -> None:
        """Override to block until the generator naturally finishes.

        Default: does nothing — the caller drives termination via stop()."""
        del timeout

    @abc.abstractmethod
    def summarize(self) -> Dict[str, Any]:
        ...

    def records(self) -> List[Dict[str, Any]]:
        return JsonlWriter.load(self._jsonl_path)

    # Convenience for subclasses.
    def _emit(self, record: Dict[str, Any]) -> None:
        record.setdefault('generator', self.name)
        record.setdefault('worker_id', self.ctx.worker_id)
        self._writer.write(record)


# ── latency utilities ──────────────────────────────────────────────


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(int(p * len(s)), len(s) - 1)
    return s[idx]


def summarize_durations(rows: List[Dict[str, Any]],
                        duration_key: str = 'duration_s') -> Dict[str, Any]:
    durs = [r[duration_key] for r in rows if duration_key in r]
    if not durs:
        return {'n': 0}
    return {
        'n': len(durs),
        'p50': round(percentile(durs, 0.50), 4),
        'p95': round(percentile(durs, 0.95), 4),
        'p99': round(percentile(durs, 0.99), 4),
        'max': round(max(durs), 4),
    }
