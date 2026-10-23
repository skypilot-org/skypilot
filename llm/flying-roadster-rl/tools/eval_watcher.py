"""Eval watcher for the train-and-eval Job Group.

Runs as a small CPU task next to the trainer and its inference server. Every
new checkpoint the trainer writes to the bucket (export/global_step_<N>/policy)
gets its own eval job, launched with `sky jobs launch` from inside the group:
each one is a dynamic task of the group on its own GPU, flying the validation
seeds, so evaluations run in parallel and never block training.

When training is done and every checkpoint is scored, the best one on
validation is flown once on the held-out test seeds, in real time, one car per
GPU (also a dynamic task).

    python tools/eval_watcher.py --run $RUN --base /outputs/$RUN/sft_model --eval-yaml eval.yaml
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The SkyPilot CLI installer puts `sky` next to the Python it installed into, which may not be on PATH yet.
SKY = shutil.which('sky') or next(
    (p for p in (os.path.join(os.path.dirname(sys.executable), 'sky'),
                 os.path.expanduser('~/.local/bin/sky')) if os.path.exists(p)),
    'sky')


def log(msg):
    print(time.strftime('%H:%M:%S'), msg, flush=True)


_sizes = {}


def ready_steps(out):
    """Checkpoints whose LoRA adapter has fully landed in the bucket: both files present, non-empty,
    and the same size as on the previous poll (so a copy still in progress is not picked up)."""
    steps = []
    for d in glob.glob(f'{out}/export/global_step_*/policy'):
        files = [
            os.path.join(d, f)
            for f in ('adapter_config.json', 'adapter_model.safetensors')
        ]
        if not all(os.path.exists(f) for f in files):
            continue
        sizes = tuple(os.path.getsize(f) for f in files)
        if all(sizes) and _sizes.get(d) == sizes:
            steps.append(int(d.split('global_step_')[1].split('/')[0]))
        _sizes[d] = sizes
    return sorted(steps)


def launch(args, name, step, tag, seeds, per_kind, modes, concurrency):
    """Launch one eval job; from inside a job group it attaches to the group as a dynamic task."""
    envs = {
        'OUTPUT_BUCKET': args.bucket,
        'RUN': args.run,
        'MODEL': args.base,
        'STEPS': str(step),
        'TAG': tag,
        'EVAL_SEEDS': seeds,
        'PER_KIND': str(per_kind),
        'WEATHERS': args.weathers,
        'MODES': modes,
        'CONCURRENCY': str(concurrency),
        'TEMPS': '0.0',
        'MERGE': '1',
        'SKIP_BASE': '1'
    }
    cmd = [SKY, 'jobs', 'launch', '-y', '-d', '-n', name, args.eval_yaml]
    for k, v in envs.items():
        cmd += ['--env', f'{k}={v}']
    for attempt in range(
            5
    ):  # the API server can be briefly unreachable; retry rather than lose a checkpoint
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
        if r.returncode == 0:
            log(f'launched {name}')
            return True
        log(f'launch {name} failed (attempt {attempt + 1}): {(r.stdout + r.stderr)[-400:]}'
           )
        time.sleep(30)
    return False


def summaries(out, tag):
    """Finished results of one eval, by weather. A summary still being written is skipped until the next poll."""
    found = {}
    for p in glob.glob(f'{out}/pilot_eval_{tag}/*/summary.json'):
        if 'latency_c1' in p:
            continue
        try:
            with open(p) as f:
                found[os.path.basename(os.path.dirname(p))] = json.load(f)
        except (OSError, ValueError):
            pass
    return found


def success(found):
    """Share of all flights that landed, over every stunt and weather in one eval."""
    ok = n = 0
    for s in found.values():
        for k in s['kinds'].values():
            ok += k['success'] * k['n']
            n += k['n']
    return ok / n if n else 0.0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run', required=True)
    ap.add_argument('--bucket', default=os.environ.get('OUTPUT_BUCKET', ''))
    ap.add_argument(
        '--base',
        required=True,
        help=
        'the model the LoRA trained on (/outputs/<RUN>/sft_model after a warm start)'
    )
    ap.add_argument('--eval-yaml', default='eval.yaml')
    ap.add_argument('--weathers', default='normal gale storm')
    ap.add_argument('--val-seeds', default='2001,2002,2003')
    ap.add_argument('--val-per-kind', type=int, default=96)
    ap.add_argument('--test-seeds',
                    default='',
                    help='"" = the eval\'s held-out seeds')
    ap.add_argument('--test-per-kind', type=int, default=96)
    ap.add_argument('--poll', type=int, default=60)
    ap.add_argument('--eval-timeout', type=int, default=3 * 3600)
    args = ap.parse_args()
    out = f'/outputs/{args.run}'
    done_marker = f'{out}/TRAIN_DONE'
    n_weathers = len(args.weathers.split())

    launched, scores = [], {}

    def collect(
    ):  # record (and report) every validation eval that has written all its results
        for step in launched:
            found = summaries(out, f'val-step{step}')
            if step not in scores and len(found) == n_weathers:
                scores[step] = success(found)
                log(f'step {step}: {scores[step]:.3f} of validation flights landed'
                   )

    while True:
        finished = os.path.exists(
            done_marker
        )  # read before listing, so no checkpoint written before it is missed
        for step in ready_steps(out):
            if step not in launched and launch(
                    args, f'eval-step-{step}', step, f'val-step{step}',
                    args.val_seeds, args.val_per_kind, 'turn', 24):
                launched.append(step)
        collect()
        if finished:
            break
        time.sleep(args.poll)
    if not launched:
        raise SystemExit('training finished without a checkpoint')

    deadline = time.time() + args.eval_timeout
    while len(scores) < len(launched) and time.time(
    ) < deadline:  # wait for the evals still running
        time.sleep(args.poll)
        collect()
    if not scores:
        raise SystemExit('no checkpoint finished its validation eval')
    best = max(scores, key=lambda s: (scores[s], s))
    json.dump({
        'validation_success': scores,
        'best_step': best
    },
              open(f'{out}/eval_watcher.json', 'w'),
              indent=1)
    log(f'best on validation: step {best} ({scores[best]:.3f}); flying the held-out test set once with it'
       )

    if not launch(args, f'test-step-{best}', best, f'test-step{best}',
                  args.test_seeds, args.test_per_kind, 'realtime', 1):
        raise SystemExit('could not launch the test eval')
    deadline = time.time() + args.eval_timeout
    while len(found := summaries(
            out, f'test-step{best}')) < n_weathers and time.time() < deadline:
        time.sleep(
            args.poll
        )  # the group stays up until the test eval has written its results
    for name, s in sorted(found.items()):
        log(f"{s['weather']}: " +
            ', '.join(f"{k} {v['success']:.3f} of {v['n']}"
                      for k, v in s['kinds'].items()))


if __name__ == '__main__':
    main()
