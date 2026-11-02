"""Warm-start data for pilot mode: successful scripted-pilot flights on TRAINING tasks.

    python skyrl_roadster/make_pilot_sft_data.py --out ~/data/pilot_sft.parquet \
        --per-kind 300 --model Qwen/Qwen3-4B-Instruct-2507

Flies ``pilot_baselines.TUNED`` on the RL training split (seed 0, never the
held-out seeds), keeps flights that landed on the road, and writes each as the
exact multi-turn conversation the RL pilot sees (briefing + observation, then
one JSON command per turn). SkyRL pretokenized rows: ``input_ids`` +
``loss_mask`` with the loss on the pilot's replies only.
"""
import argparse
import json
import os
import random
import sys

import pandas as pd
from transformers import AutoTokenizer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompting import build_pilot_messages  # noqa: E402
from roadster_env.pilot import obs_text  # noqa: E402
from roadster_env.pilot import PilotEpisode
from roadster_env.pilot_baselines import TUNED  # noqa: E402
from roadster_env.tasks import REVEAL_KINDS  # noqa: E402
from roadster_env.tasks import sample_task


def compact(action, digits):
    """Round floats in a command. Full precision ("roll": -2.883665561946049) teaches
    the pilot to spend ~15 extra tokens on every landing command. digits < 0 keeps
    full precision (what the blog post's numbers used)."""
    if digits < 0:
        return action
    return {
        k: (round(v, digits) if isinstance(v, float) else v)
        for k, v in action.items()
    }


def conversation(task, digits=-1):
    """Fly the scripted pilot; return (messages, result)."""
    ep, mem, pilot = PilotEpisode(task), {}, TUNED[task['kind']]
    msgs = build_pilot_messages(task, obs_text(ep.obs, task))
    while not ep.done:
        action = compact(pilot(ep.obs, mem), digits)
        ep.step(action)
        msgs.append({'role': 'assistant', 'content': json.dumps(action)})
        if not ep.done:
            msgs.append({
                'role': 'user',
                'content': obs_text(ep.obs, task, ep.action)
            })
    return msgs, ep.result


def replay(task, actions, digits):
    """Re-fly a teacher's recorded decisions (sim-waits mode) with rounded numbers;
    return (messages, result) so the conversation matches what the pilot sees."""
    ep = PilotEpisode(task)
    msgs = build_pilot_messages(task, obs_text(ep.obs, task))
    for a in actions:
        if ep.done:
            break
        a = compact(a, digits) if a else None
        ep.step(a)
        msgs.append({
            'role': 'assistant',
            'content': json.dumps(a if a else ep.action)
        })
        if not ep.done:
            msgs.append({
                'role': 'user',
                'content': obs_text(ep.obs, task, ep.action)
            })
    while not ep.done:  # teacher log ended early: the last command keeps flying
        ep.step(None)
    return msgs, ep.result


def tokenize(tok, msgs, max_length):
    """input_ids + loss_mask (1 on assistant replies), checking the prefix property."""
    ids = tok(tok.apply_chat_template(msgs, tokenize=False),
              add_special_tokens=False)['input_ids']
    mask = [0] * len(ids)
    for i, m in enumerate(msgs):
        if m['role'] != 'assistant':
            continue
        start = tok(tok.apply_chat_template(msgs[:i],
                                            add_generation_prompt=True,
                                            tokenize=False),
                    add_special_tokens=False)['input_ids']
        end = tok(tok.apply_chat_template(msgs[:i + 1], tokenize=False),
                  add_special_tokens=False)['input_ids']
        if ids[:len(end)] != end or end[:len(start)] != start:
            return None
        for j in range(len(start), len(end)):
            mask[j] = 1
    return None if len(ids) > max_length else (ids, mask)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--per-kind',
                    type=int,
                    default=300,
                    help='training tasks flown per stunt')
    ap.add_argument('--model', default='Qwen/Qwen3-4B-Instruct-2507')
    ap.add_argument('--max-length', type=int, default=10240)
    ap.add_argument(
        '--teacher',
        default='',
        help=
        'jsonl of teacher flights ({"task", "actions"}) recorded in sim-waits mode on non-held-out '
        'seeds; replaces the scripted pilot for --teacher-kinds')
    ap.add_argument('--teacher-kinds', default='hop_kickflip')
    ap.add_argument(
        '--teacher-adds',
        action='store_true',
        help=
        'add the teacher flights on top of the scripted ones instead of replacing the script for those kinds'
    )
    ap.add_argument(
        '--weathers',
        default='normal',
        help=
        'scripted flights cycle through these weathers (only successful flights are kept)'
    )
    ap.add_argument(
        '--round',
        type=int,
        default=-1,
        help='round command numbers to this many decimals (-1 = full precision)'
    )
    args = ap.parse_args()
    tok = AutoTokenizer.from_pretrained(args.model)
    rng = random.Random(0)  # = the RL training split (make_dataset seed 0)
    rows, kept, skipped = [], {}, 0
    teacher_kinds = set(
        args.teacher_kinds.split(',')) if args.teacher else set()
    jobs = [(t['task'], t['actions'])
            for t in map(json.loads,
                         open(args.teacher) if args.teacher else [])
            if t['task']['kind'] in teacher_kinds]
    for kind in REVEAL_KINDS:
        if kind not in teacher_kinds or args.teacher_adds:
            weathers = args.weathers.split(',')
            jobs += [(sample_task(kind,
                                  rng,
                                  weather=weathers[i % len(weathers)]), None)
                     for i in range(args.per_kind)]
    for task, actions in jobs:
        kind = task['kind']
        if True:
            msgs, res = replay(
                task, actions,
                args.round) if actions is not None else conversation(
                    task, args.round)
            if not res.get('success'):
                continue
            t = tokenize(tok, msgs, args.max_length)
            if t is None:
                skipped += 1
                continue
            rows.append({'input_ids': t[0], 'loss_mask': t[1]})
            kept[kind] = kept.get(kind, 0) + 1
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    pd.DataFrame(rows).to_parquet(args.out)
    print(f'wrote {len(rows)} flights {kept} (skipped {skipped}) to {args.out}')


if __name__ == '__main__':
    main()
