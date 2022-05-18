"""Sky benchmark callback log parsers."""
import glob
import json
import os
import shlex
from typing import Dict, List

from sky.skylet.callbacks.sky_callback import base as sky_callback


def _dict_to_json(json_dict: Dict[str, int], output_path: str):
    json_str = json.dumps(json_dict)
    with open(output_path, 'w') as f:
        f.write(json_str)


def parse_log(log_dir: str, output_path: str) -> None:
    log_dirs = glob.glob(os.path.join(log_dir, 'sky-*'))
    if len(log_dirs) == 0:
        raise ValueError(f'No callback logs found in {log_dir}.')

    # Use the latest log.
    log_dir = sorted(log_dirs)[-1]
    timestamp_log = os.path.join(log_dir, sky_callback.TIMESTAMP_LOG)

    timestamps = []
    with open(timestamp_log, 'rb') as f:
        while True:
            b = f.read(sky_callback.NUM_BYTES_PER_TIMESTAMP)
            ts = int.from_bytes(b, sky_callback.BYTE_ORDER)
            if ts == 0:
                # EOF
                break
            else:
                timestamps.append(ts)
    if len(timestamps) < 2:
        raise ValueError(f'Not enough timestamps found in {f}.')

    summary = {}
    summary['start_ts'] = int(timestamps[0])
    summary['first_ts'] = int(timestamps[1])
    summary['last_ts'] = int(timestamps[-1])
    summary['iters'] = len(timestamps[1:])
    _dict_to_json(summary, output_path)


class BenchmarkCodeGen:
    """Code generator for benchmark log parsers.

    Usage:

      >> codegen = BenchmarkCodeGen.generate_summary(...)
    """

    _PREFIX = ['from sky.skylet import benchmark_lib']

    @classmethod
    def generate_summary(cls, log_dir: str, output_path: str) -> None:
        """Generate a summary of the log."""
        code = [
            'import os',
            f'log_dir = os.path.expanduser({log_dir!r})',
            f'output_path = os.path.expanduser({output_path!r})',
            'os.makedirs(os.path.dirname(output_path), exist_ok=True)',
            f'benchmark_lib.parse_log(log_dir, output_path)',
        ]
        return cls._build(code)

    @classmethod
    def _build(cls, code: List[str]) -> str:
        code = cls._PREFIX + code
        code = ';'.join(code)
        return f'python3 -u -c {shlex.quote(code)}'
