"""Write SkyRL train/val parquet files for the roadster pilot environment.

    python skyrl_roadster/make_dataset.py --out ~/data/pilot --val-seeds 2001,2002,2003

Each row's prompt is the pilot briefing plus the first observation; the
environment (``pilot_env.py``) flies the rest. Validation uses fixed seeds and
one data_source per stunt and weather (``roadster/<kind>[_gale|_storm]``), so
SkyRL reports per-stunt, per-weather eval metrics. The held-out test seeds
(1234, 4321, 5678; see ``tools/pilot_bench.py``) are the default; pass other
``--val-seeds`` to pick checkpoints on a separate validation split.
"""
import argparse
import json
import os
import random
import sys

from datasets import Dataset

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompting import build_pilot_messages  # noqa: E402
from roadster_sim.pilot import handover, obs_text  # noqa: E402
from roadster_sim.tasks import REVEAL_KINDS, TASK_KINDS, sample_task  # noqa: E402

HARD_KICKFLIP_S = 2.4  # seconds until the truck: a slow car and a far truck, the trained pilot's weak spot


def rows(n_per_kind, seed: int, kinds, weather='normal', handover_frac=0.0, max_handover=None,
         hard_frac=0.0):
    """``n_per_kind``: an int, or a {kind: count} dict for a weighted mix.

    ``handover_frac`` of the rows (training only) start mid-flight: the scripted
    pilot flies the first k decisions, k uniform in [1, max_handover[kind]], and
    the model takes over (reverse curriculum). ``hard_frac`` of the Cybertruck
    rows (training only) are resampled until the truck is >= HARD_KICKFLIP_S away."""
    rng = random.Random(seed)
    tasks = []
    for kind in kinds:
        n = n_per_kind[kind] if isinstance(n_per_kind, dict) else n_per_kind
        tasks += [sample_task(kind, rng, weather=weather) for _ in range(n)]
    rng.shuffle(tasks)
    hrng, hard_rng = random.Random(seed + 1), random.Random(seed + 2)
    suffix = '' if weather == 'normal' else f'_{weather}'
    out = []
    for task in tasks:
        kind = task['kind']
        if kind == 'hop_kickflip' and hard_frac and hard_rng.random() < hard_frac:
            while task['obstacle']['x0'] / task['speed'] < HARD_KICKFLIP_S:
                task = sample_task(kind, hard_rng, weather=weather)
        k = 0
        if max_handover and max_handover.get(kind) and hrng.random() < handover_frac:
            k = hrng.randint(1, max_handover[kind])
        ep = handover(task, k)
        if ep.done:  # the script finished the flight: nothing left to learn
            k, ep = 0, handover(task, 0)
        out.append({
            'prompt': build_pilot_messages(task, obs_text(ep.obs, task, ep.action if k else None),
                                           handover=bool(k)),
            'env_class': 'roadster_pilot',
            'data_source': f'roadster/{kind}{suffix}',
            'task_json': json.dumps(task),
            'handover_steps': k,
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.expanduser('~/data/pilot'))
    ap.add_argument('--handover', default='',
                    help='training rows only: "frac:kind=max,..." e.g. "0.5:hop_kickflip=20" '
                         'starts half the Cybertruck flights after 1-20 scripted decisions')
    ap.add_argument('--train-per-kind', type=int, default=2000)
    ap.add_argument('--train-weathers', default='normal',
                    help='comma-separated weathers for the training rows, split evenly (seed 0, 10, 20, ...)')
    ap.add_argument('--val-per-kind', type=int, default=24)
    ap.add_argument('--val-gale-per-kind', type=int, default=24,
                    help='gale-force-wind eval tasks per kind')
    ap.add_argument('--val-storm-per-kind', type=int, default=24,
                    help='storm-force-wind eval tasks per kind')
    ap.add_argument('--val-seeds', default='1234,4321,5678',
                    help='calm,gale,storm seeds for the in-training eval set. The defaults are the held-out '
                         'test seeds; pass others (e.g. 2001,2002,2003) to pick checkpoints on a separate '
                         'validation split and keep the held-out seeds for the final eval only')
    ap.add_argument('--kinds', default=','.join(REVEAL_KINDS),
                    help=f'comma-separated subset of {TASK_KINDS}')
    ap.add_argument('--kind-weights', default='',
                    help='e.g. "hop_kickflip=3,ramp_flip=1": training mix weights '
                    '(total size stays train_per_kind * len(kinds))')
    ap.add_argument('--hard-frac', type=float, default=0.0,
                    help='fraction of Cybertruck training rows with a slow car and a far truck')
    args = ap.parse_args()
    kinds = args.kinds.split(',')
    assert set(kinds) <= set(TASK_KINDS), kinds
    os.makedirs(args.out, exist_ok=True)
    train_n = args.train_per_kind
    if args.kind_weights:
        w = {k: float(v) for k, v in (kv.split('=') for kv in args.kind_weights.split(','))}
        total = args.train_per_kind * len(kinds)
        train_n = {k: round(total * w.get(k, 0) / sum(w.get(x, 0) for x in kinds)) for k in kinds}
        print('training mix:', train_n)
    frac, maxk = 0.0, None
    if args.handover:
        f, spec = args.handover.split(':')
        frac, maxk = float(f), {k: int(v) for k, v in (kv.split('=') for kv in spec.split(','))}
    weathers = args.train_weathers.split(',')
    per = lambda n: max(1, n // len(weathers))  # noqa: E731
    train_per = {k: per(v) for k, v in train_n.items()} if isinstance(train_n, dict) else per(train_n)
    train = []
    for i, weather in enumerate(weathers):  # seed 0 for calm keeps the calm-only training rows unchanged
        train += rows(train_per, 10 * i, kinds, weather, frac, maxk, args.hard_frac)
    random.Random(0).shuffle(train)
    Dataset.from_list(train).to_parquet(os.path.join(args.out, 'train.parquet'))
    vc, vg, vs = (int(x) for x in args.val_seeds.split(','))
    assert 0 not in {vc, vg, vs}, 'seed 0 is the training split'
    val = (rows(args.val_per_kind, vc, kinds) +
           rows(args.val_gale_per_kind, vg, kinds, 'gale') +
           rows(args.val_storm_per_kind, vs, kinds, 'storm'))
    Dataset.from_list(val).to_parquet(os.path.join(args.out, 'validation.parquet'))
    print('wrote', args.out)


if __name__ == '__main__':
    main()
