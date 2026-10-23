"""Validate a job-group YAML's SkyRL overrides locally, before burning a GPU launch.

Renders the trainer task's `main_roadster` command with the task envs (plus
KEY=VALUE overrides) through bash, exactly as the job would, then runs it
through SkyRL's own `SkyRLTrainConfig.from_cli_overrides` + `validate_cfg`.

    # in a Python 3.12 env with SkyRL (CPU-only is fine) + pyyaml installed:
    WANDB_API_KEY=dummy python tools/check_skyrl_config.py roadster-rl.yaml MAX_TURNS=3
"""
import os
import re
import subprocess
import sys

import yaml

ENTRY = re.compile(
    r'uv run --isolated --extra \w+ -m examples\.train\.roadster\.main_roadster'
)


def validate(argv):
    from skyrl.train.config import SkyRLTrainConfig
    from skyrl.train.utils.utils import validate_cfg
    cfg = SkyRLTrainConfig.from_cli_overrides(argv)
    validate_cfg(cfg)
    g, t = cfg.generator, cfg.trainer
    print(
        f'OK colocate={t.placement.colocate_all} local_engines={g.inference_engine.run_engines_locally} '
        f'lora_r={t.policy.model.lora.rank} sync={t.policy.model.lora.lora_sync_path} '
        f'gen_len={g.sampling_params.max_generate_length} max_turns={g.max_turns} logger={t.logger}'
    )


def main():
    if sys.argv[1] == '--validate':
        return validate(sys.argv[2:])
    path, extra = sys.argv[1], dict(a.split('=', 1) for a in sys.argv[2:])
    docs = [d for d in yaml.safe_load_all(open(path)) if d]
    # Job Group: the task named `trainer`; single-task YAML: the only document.
    trainer = next((d for d in docs if d.get('name') == 'trainer'), docs[-1])
    run = trainer['run']
    m = ENTRY.search(run)
    cmd = f'{sys.executable} {os.path.abspath(__file__)} --validate' + run[
        m.end():]
    env = {
        **os.environ,
        **{k: str(v) for k, v in (trainer.get('envs') or {}).items()}, 'DATA': '/tmp/data',
        'OUT': '/outputs/x',
        'GW': 'http://gw:8000',
        'SKYPILOT_JOBGROUP_NAME': 'g',
        'PROXY': 'http://10.0.0.1:8001',
        'SERVERS': "['http://10.0.0.1:8000']",
        **extra
    }
    r = subprocess.run(['bash', '-c', 'set -e\n' + cmd],
                       env=env,
                       capture_output=True,
                       text=True)
    print(path, extra, '->',
          r.stdout.strip() or r.stderr.strip().splitlines()[-1])
    sys.exit(r.returncode)


if __name__ == '__main__':
    main()
