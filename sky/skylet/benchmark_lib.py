import glob
import json
import os
import shlex
from typing import Dict, List


def _dict_to_json(json_dict: Dict[str, int], output_path: str):
    json_str = json.dumps(json_dict)
    with open(output_path, 'w') as f:
        f.write(json_str)


def summarize_tensorboard(log_dir: str, output_path: str) -> None:
    import pandas as pd
    from tensorboard.backend.event_processing import event_accumulator

    event_files = glob.glob(os.path.join(log_dir, 'events.out.tfevents.*'))
    if not event_files:
        raise ValueError(f'No tensorboard logs found in {log_dir}.')

    # Use the latest event file.
    event_file = event_files[-1]

    ea = event_accumulator.EventAccumulator(
        event_file, size_guidance={event_accumulator.SCALARS: 0})
    ea.Reload()
    scalar = ea.Tags()['scalars'][0]
    df = pd.DataFrame(ea.Scalars(scalar))
    timestamps = df['wall_time']

    summary = {}
    summary['start_ts'] = int(os.path.basename(event_file).split('.')[3])
    summary['first_ts'] = int(timestamps.iloc[0])
    summary['last_ts'] = int(timestamps.iloc[-1])
    summary['iters'] = len(timestamps)
    _dict_to_json(summary, output_path)


def summarize_wandb(log_dir: str, output_path: str) -> None:
    import pandas as pd

    # Use the latest wandb log.
    wandb_summary = os.path.join(log_dir, 'latest-run/files/wandb-summary.json')
    wandb_summary = pd.read_json(wandb_summary, lines=True)
    assert len(wandb_summary) == 1
    wandb_summary = wandb_summary.iloc[0]

    summary = {}
    summary['last_ts'] = int(wandb_summary['_timestamp'])
    summary['iters'] = int(wandb_summary['_step'])
    summary['start_ts'] = int(wandb_summary['_timestamp'] - wandb_summary['_runtime'])

    # FIXME: This is a hack.
    from wandb.proto import wandb_internal_pb2
    from wandb.sdk.internal import datastore

    wandb_logs = glob.glob(os.path.join(log_dir, 'latest-run', 'run-*.wandb'))
    assert len(wandb_logs) == 1
    wandb_log = wandb_logs[0]

    ds = datastore.DataStore()
    ds.open_for_scan(wandb_log)
    pb = wandb_internal_pb2.Record()

    found_first_ts = False
    while not found_first_ts:
        _, data = ds.scan_record()
        pb.ParseFromString(data)
        if pb.WhichOneof('record_type') == 'history':
            for item in pb.history.item:
                if item.key == '_timestamp':
                    summary['first_ts'] = int(item.value_json)
                    found_first_ts = True
                    break
    _dict_to_json(summary, output_path)


class BenchmarkCodeGen:

    _PREFIX = ['from sky.skylet import benchmark_lib']

    @classmethod
    def generate_summary(cls, log_dir: str, output_path: str, logger_name: str) -> None:
        """Generate a summary of the log."""
        assert logger_name in ['tensorboard', 'wandb']
        code = [
            'import os',
            f'log_dir = os.path.expanduser({log_dir!r})',
            f'output_path = os.path.expanduser({output_path!r})',
            f'os.makedirs(os.path.dirname(output_path), exist_ok=True)',
            f'benchmark_lib.summarize_{logger_name}(log_dir, output_path)',
        ]
        return cls._build(code)

    @classmethod
    def _build(cls, code: List[str]) -> str:
        code = cls._PREFIX + code
        code = ';'.join(code)
        return f'python3 -u -c {shlex.quote(code)}'
