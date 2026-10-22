r"""Fly held-out stunts with an API model as the pilot (one decision per 0.2 s).

    SKYPILOT_TOKENS_API_KEY=... python tools/pilot_bench.py \
        --model deepseek-ai/DeepSeek-V4.1-Flash-1017 --realtime --out /tmp/pilot_deepseek
    OPENROUTER_API_KEY=... python tools/pilot_bench.py --endpoint openrouter \
        --model openai/gpt-6-astra --reasoning low --per-kind 12 --realtime --out /tmp/pilot_astra
    OPENROUTER_API_KEY=... python tools/pilot_bench.py --endpoint openrouter --model '~typesafe/jev-latest' \
        --realtime --out /tmp/pilot_jev
    python tools/pilot_bench.py --base-url http://localhost:8001/v1 --model step30 --out /tmp/pilot_local

--endpoint picks the API and its defaults (see ENDPOINTS):
  tokens      https://tokens.skypilot.ai/v1 (default), key in $SKYPILOT_TOKENS_API_KEY
              (create one at https://tokens.skypilot.ai). Its reasoning models think long, so
              max_tokens defaults to 16384, and requests are paced under the per-key limit
              (120/min per model) with extra retries on 429.
  openrouter  https://openrouter.ai/api/v1, key in $OPENROUTER_API_KEY.
  custom      any OpenAI-compatible --base-url (implied by --base-url alone), key in
              $OPENAI_API_KEY. A local server (localhost / 127.0.0.1) needs no key.
--base-url, --api-key-env, --max-tokens, --rpm and --retries override the preset.

Chat models: each decision is one chat turn; the model sees the conversation so
far (briefing, then one observation line per turn) and replies with one JSON
action (--interface json) or one pick from the action menu (--interface menu,
see roadster_env/pilot_menu.py).

Decision models (`~typesafe/...`, e.g. Jev, on OpenRouter's /alpha/decisions
endpoint) always fly the menu: every decision is two typed `choice` questions
(manoeuvre, drift correction) over the briefing, the latest observation and the
last few picks.

--realtime  the car doesn't wait: while a decision is in flight the previous
            action keeps flying for ceil(latency / 0.2 s) intervals, as it would
            on a real vehicle. The first decision is the pre-flight plan, made
            before launch, so it is not timed. Without --realtime the sim pauses
            for every decision (turn-based: measures decision quality alone).

Reasoning models get their earlier turns' reasoning back (OpenRouter
`reasoning_details`), as providers recommend for multi-turn use; some (Beam)
reject a conversation without it.

The pilot only emits JSON actions (no code), so the simulator runs in-process.
Writes <out>/summary.json and <out>/episodes.jsonl.
"""
import argparse
import json
import math
import os
import random
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from prompting import build_pilot_messages  # noqa: E402
from prompting import pilot_system_prompt, user_prompt  # noqa: E402
from roadster_env.pilot import DECISION_DT, ActionError, PilotEpisode, obs_text, parse_action  # noqa: E402
from roadster_env.pilot_menu import DRIFT, MANOEUVRES, menu_action, menu_text  # noqa: E402
from roadster_env.tasks import REVEAL_KINDS, sample_task  # noqa: E402

WEATHER_SEEDS = {'normal': 1234, 'gale': 4321, 'storm': 5678}  # = make_dataset
# --endpoint presets: base URL, key variable, then defaults for flags left unset.
# tokens.skypilot.ai allows 120 requests/min per key and model: pace at 110, retry 429s longer.
ENDPOINTS = {
    'tokens': dict(base_url='https://tokens.skypilot.ai/v1', key_env='SKYPILOT_TOKENS_API_KEY',
                   max_tokens=16384, rpm=110, retries=8),
    'openrouter': dict(base_url='https://openrouter.ai/api/v1', key_env='OPENROUTER_API_KEY',
                       max_tokens=4096, rpm=0, retries=4),
    'custom': dict(base_url='', key_env='OPENAI_API_KEY', max_tokens=4096, rpm=0, retries=4),
}
MENU_REPLY = ('Reply with exactly one JSON object and nothing else: '
              '{"manoeuvre": "<name>", "drift": "<name>"}.')


def briefing(task):
    return user_prompt(task).replace('\n\nWrite the controller.', '')


def vehicle_rules(task):
    """The pilot system prompt minus the free-form JSON action section."""
    return pilot_system_prompt(task).split('## Actions')[0].strip()


def menu_messages(task, first_obs):
    system = f'{vehicle_rules(task)}\n\n## Actions\nEvery 0.2 s pick one manoeuvre and one drift correction.\n' \
             f'{menu_text()}\n\n{MENU_REPLY}'
    return [{'role': 'system', 'content': system},
            {'role': 'user', 'content': f'{briefing(task)}\n\nYou are flying now.\n{first_obs}'}]


def parse_menu(text):
    a = json.loads(_last_json(text))
    if a.get('manoeuvre') not in MANOEUVRES:
        raise ActionError('unknown manoeuvre')
    return menu_action(a['manoeuvre'], a.get('drift', 'none')), a['manoeuvre'], a.get('drift', 'none')


def _last_json(text):
    import re  # pylint: disable=import-outside-toplevel
    blobs = re.findall(r'\{.*?\}', re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL), re.DOTALL)
    if not blobs:
        raise ActionError('no JSON')
    return blobs[-1]


def resolve_endpoint(args, ap):
    """Fill --base-url / --api-key-env / --max-tokens / --rpm / --retries from the --endpoint preset."""
    if not args.endpoint:  # decision models (~typesafe/...) only exist on OpenRouter
        args.endpoint = 'custom' if args.base_url else 'openrouter' if args.model.startswith('~typesafe/') else 'tokens'
    preset = ENDPOINTS[args.endpoint]
    args.base_url = (args.base_url or preset['base_url']).rstrip('/')
    if not args.base_url:
        ap.error('--endpoint custom needs --base-url')
    for k in ('max_tokens', 'rpm', 'retries'):
        if getattr(args, k) is None:
            setattr(args, k, preset[k])


def api_headers(args):
    """Authorization header for --base-url; none for a local server without a key."""
    openrouter = 'openrouter.ai' in args.base_url
    default_env = 'OPENROUTER_API_KEY' if openrouter else ENDPOINTS[args.endpoint]['key_env']
    env = args.api_key_env or default_env
    key = os.environ.get(env)
    if not key and openrouter and not args.api_key_env:  # older instructions used OPENAI_API_KEY
        key = os.environ.get('OPENAI_API_KEY')
    if key:
        return {'Authorization': f'Bearer {key}'}
    if urlparse(args.base_url).hostname in ('localhost', '127.0.0.1'):
        return {}
    hint = ' (create a key at https://tokens.skypilot.ai)' if 'tokens.skypilot.ai' in args.base_url else ''
    sys.exit(f'${env} is not set: API key for {args.base_url}{hint}; --api-key-env reads another variable')


def decide(args, task, obs_line, history):
    """One Jev decision -> (action, picks, latency_s, cost_usd, error)."""
    body = {'model': args.model,
            'state': {'stunt': briefing(task), 'vehicle': vehicle_rules(task),
                      'observation': obs_line, 'recent_picks': history[-6:]},
            'questions': {
                'manoeuvre': {'type': 'choice', 'criteria': {k: d for k, (d, _) in MANOEUVRES.items()},
                              'instructions': 'Which manoeuvre should the flight computer fly for the next '
                                              '0.2 s to complete the stunt and land on the road?'},
                'drift': {'type': 'choice', 'criteria': DRIFT,
                          'instructions': 'Which sideways correction keeps the car over the road?'}}}
    err = None
    for attempt in range(4):
        t0 = time.monotonic()
        try:
            r = requests.post(f'{args.base_url.replace("/v1", "")}/alpha/decisions', json=body, timeout=60,
                              headers=args.headers)
            lat = time.monotonic() - t0
            if r.status_code >= 400:
                err = f'HTTP {r.status_code}: {r.text[:200]}'
                if r.status_code in (400, 401, 403, 404):
                    break
                time.sleep(min(2 ** attempt, 30))
                continue
            d = r.json()
            m, dr = d['answers']['manoeuvre']['choice'], d['answers']['drift']['choice']
            return (menu_action(m, dr), f'{m}/{dr}', lat,
                    float((d.get('usage') or {}).get('cost') or 0.0), None)
        except (requests.RequestException, KeyError, TypeError) as e:
            err = f'{type(e).__name__}: {e}'[:200]
    return None, None, 0.0, 0.0, err


def eval_tasks(seed, weather, per_kind):
    """``per_kind`` tasks per stunt. The first 24 are exactly make_dataset's
    held-out tasks for this seed (one rng across both stunts); tasks beyond 24
    come from a separate stream (seed + 7919, per stunt) so asking for more
    flights never changes the first 24."""
    rng = random.Random(seed)
    base = [sample_task(k, rng, weather=weather) for k in REVEAL_KINDS for _ in range(24)]
    out = []
    for i, k in enumerate(REVEAL_KINDS):
        mine = [t for t in base if t['kind'] == k][:per_kind]
        extra = random.Random(seed + 7919 + i)
        mine += [sample_task(k, extra, weather=weather) for _ in range(max(0, per_kind - 24))]
        out += mine
    return out


def normalize_or_none(action):
    return None if action is None else dict(action)


def vote(replies):
    """Majority command (mode + axis) over sampled replies; numbers are the median among the winners."""
    acts = []
    for text in replies:
        try:
            acts.append(parse_action(text))
        except (ActionError, ValueError, AttributeError):
            pass
    if not acts:
        return replies[0] if replies else ''
    key = lambda a: (a.get('mode'), a.get('axis'))  # noqa: E731
    top = max(set(map(key, acts)), key=lambda k: sum(key(a) == k for a in acts))
    win = [a for a in acts if key(a) == top]
    out = dict(win[0])
    for f, v in out.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            vals = sorted(a[f] for a in win if isinstance(a.get(f), (int, float)))
            out[f] = vals[len(vals) // 2]
    return json.dumps(out)


_rate_lock = threading.Lock()
_rate_next = [0.0]


def rate_limit(rpm):
    """Space requests evenly across threads to stay under an endpoint's requests-per-minute cap."""
    if not rpm:
        return
    with _rate_lock:
        now = time.monotonic()
        slot = max(now, _rate_next[0])
        _rate_next[0] = slot + 60.0 / rpm
    time.sleep(max(0.0, slot - now))


def chat(args, messages):
    """-> (assistant message, latency_s, cost_usd, error)."""
    body = {'model': args.model, 'messages': messages, 'temperature': args.temperature,
            'max_tokens': args.max_tokens}
    if args.vote > 1:
        body.update(n=args.vote, temperature=args.vote_temperature)
    if args.provider:  # one provider per run: reasoning blobs don't carry across providers
        body['provider'] = {'order': [args.provider], 'allow_fallbacks': False}
    if 'openrouter.ai' not in args.base_url:  # OpenAI-style servers (e.g. tokens.skypilot.ai): reasoning_effort
        if args.reasoning != 'default':
            body['reasoning_effort'] = 'none' if args.reasoning == 'off' else args.reasoning
    elif args.reasoning == 'off':
        body['reasoning'] = {'enabled': False}
    elif args.reasoning in ('low', 'medium', 'high', 'max'):
        body['reasoning'] = {'effort': args.reasoning}
    err = None
    for attempt in range(args.retries):
        rate_limit(args.rpm)  # before t0: waiting for a slot is not the model's latency
        t0 = time.monotonic()
        try:
            r = requests.post(f'{args.base_url}/chat/completions', json=body, timeout=600,
                              headers=args.headers)
            lat = time.monotonic() - t0
            if r.status_code >= 400:
                err = f'HTTP {r.status_code}: {r.text[:200]}'
                if r.status_code in (400, 401, 403, 404):
                    break
                time.sleep(min(2 ** attempt, 30))
                continue
            d = r.json()
            m = d['choices'][0]['message']
            msg = {'role': 'assistant', 'content': m.get('content') or ''}
            if args.vote > 1:  # self-consistency: majority command over the samples, median of its numbers
                msg['content'] = vote([c['message'].get('content') or '' for c in d['choices']])
            for key in ('reasoning', 'reasoning_details'):
                if m.get(key):
                    msg[key] = m[key]
            return msg, lat, float((d.get('usage') or {}).get('cost') or 0.0), None
        except (requests.RequestException, KeyError, IndexError) as e:
            err = f'{type(e).__name__}: {e}'[:200]
    return {'role': 'assistant', 'content': ''}, 0.0, 0.0, err


def fly_one(args, idx, task):
    ep = PilotEpisode(task, record=True)
    first = obs_text(ep.obs, task)
    log = []  # one entry per decision: when it was asked, when it took effect, what, how long
    msgs = menu_messages(task, first) if args.interface == 'menu' else build_pilot_messages(task, first)
    lats, cost, invalid, late, errors, history = [], 0.0, 0, 0, [], []
    while not ep.done:
        t_asked = ep.obs['t']
        if args.decisions:
            action, picks, lat, c, err = decide(args, task, obs_text(ep.obs, task), history)
            text = picks or ''
            invalid += action is None and not err
        else:
            reply, lat, c, err = chat(args, msgs)
            text = reply['content']
            try:
                if args.interface == 'menu':
                    action, m, dr = parse_menu(text)
                    picks = f'{m}/{dr}'
                else:
                    action, picks = parse_action(text), None
            except (ActionError, ValueError, AttributeError):
                action, picks, invalid = None, None, invalid + 1
        cost += c
        if err:
            errors.append(err)
            if len(errors) >= 3:
                break
        lats.append(lat)
        if picks:
            history.append(f't={ep.obs["t"]:.1f}s {picks}')
        if args.realtime and len(lats) > 1:  # the previous action keeps flying while the model thinks
            wait = max(0, math.ceil(lat / DECISION_DT) - 1)
            for _ in range(wait):
                if ep.done:
                    break
                ep.step(None)
                late += 1
        if ep.done:
            if args.realtime and len(lats) > 1:  # the reply came back after the flight ended
                log.append({'t_asked': round(t_asked, 2), 't_applied': round(t_asked + math.ceil(lat / DECISION_DT) * DECISION_DT, 2),
                            'latency_s': round(lat, 3), 'action': normalize_or_none(action), 'picks': picks, 'too_late': True})
            break
        ep.invalid = invalid
        log.append({'t_asked': round(t_asked, 2), 't_applied': round(ep.obs['t'], 2),
                    'latency_s': round(lat, 3), 'action': normalize_or_none(action), 'picks': picks,
                    **({'error': err} if err else {})})
        ep.step(action)
        if args.decisions:
            continue
        obs_line = obs_text(ep.obs, task, ep.action)
        if err:  # no reply to keep: fold the new observation into the pending user turn
            msgs = msgs[:-1] + [{'role': 'user', 'content': f'{msgs[-1]["content"]}\n{obs_line}'}]
        else:
            msgs = msgs + [{**reply, 'content': reply['content'] or '{}'},
                           {'role': 'user', 'content': obs_line}]
    r = ep.result or {'task': task, 'success': False, 'reward': 0.0, 'crashed': 'api_error'}
    if ep.result:  # a renderable clip (render_video.py) + the decision stream
        os.makedirs(os.path.join(args.out, 'clips'), exist_ok=True)
        with open(os.path.join(args.out, 'clips', f'{task["kind"]}__{idx:02d}.json'), 'w') as f:
            json.dump({**ep.result, 'pilot': args.model, 'realtime': args.realtime,
                       'decision_log': log}, f)
    return {'task': task, 'success': bool(r.get('success')), 'reward': r.get('reward', 0.0),
            'crashed': r.get('crashed'), 'landed': r.get('landed'), 'on_road': r.get('on_road'),
            'decisions': len(lats), 'late_intervals': late, 'invalid_actions': invalid,
            'latency_s_median': round(statistics.median(lats), 3) if lats else None,
            'cost_usd': round(cost, 6), 'api_errors': errors[:2], 'picks': history}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('Chat models:')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--model', required=True, help='model id, e.g. deepseek-ai/DeepSeek-V4.1-Flash-1017 '
                                                   '(GET <base-url>/models lists them)')
    ap.add_argument('--endpoint', choices=sorted(ENDPOINTS),
                    help='API preset: tokens = tokens.skypilot.ai (default), openrouter, or custom '
                         '(any --base-url; implied when --base-url is given alone)')
    ap.add_argument('--base-url', default='', help="override the preset's OpenAI-compatible base URL")
    ap.add_argument('--api-key-env', default='',
                    help='env var holding the API key (default: SKYPILOT_TOKENS_API_KEY for tokens, '
                         'OPENROUTER_API_KEY for openrouter, else OPENAI_API_KEY; a local server needs none)')
    ap.add_argument('--provider', default='', help='pin one OpenRouter provider, e.g. Together')
    ap.add_argument('--reasoning', default='default', choices=['default', 'off', 'low', 'medium', 'high', 'max'])
    ap.add_argument('--weather', default='normal', choices=sorted(WEATHER_SEEDS))
    ap.add_argument('--seeds', default='',
                    help='calm,gale,storm task seeds overriding the held-out ones, e.g. 2001,2002,2003 '
                         '(a validation split for picking checkpoints)')
    ap.add_argument('--per-kind', type=int, default=12, help='held-out tasks per stunt (first N)')
    ap.add_argument('--kinds', default='', help='comma-separated stunts to fly, e.g. ramp_flip (default: all)')
    ap.add_argument('--temperature', type=float, default=1.0)
    ap.add_argument('--vote', type=int, default=1, help='sample this many replies per decision and take the majority command')
    ap.add_argument('--vote-temperature', type=float, default=0.6)
    ap.add_argument('--max-tokens', type=int, default=None,
                    help='reply cap (default: 16384 on tokens, whose reasoning models think long; else 4096)')
    ap.add_argument('--interface', default='json', choices=['json', 'menu'],
                    help='chat models only; decision models always fly the menu')
    ap.add_argument('--realtime', action='store_true')
    ap.add_argument('--rpm', type=float, default=None,
                    help="cap this process's requests per minute (default: 110 on tokens, under its 120/min "
                         "per-key limit; else 0 = no cap)")
    ap.add_argument('--retries', type=int, default=None,
                    help='attempts per decision on 429/5xx, backoff 1, 2, 4, ... 30 s (default: 8 on tokens, else 4)')
    ap.add_argument('--concurrency', type=int, default=8, help='episodes in parallel')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    resolve_endpoint(args, ap)
    args.headers = api_headers(args)
    args.decisions = args.model.startswith('~typesafe/')
    if args.decisions:
        args.interface = 'menu'
    os.makedirs(args.out, exist_ok=True)
    seeds = dict(zip(('normal', 'gale', 'storm'), map(int, args.seeds.split(',')))) if args.seeds else WEATHER_SEEDS
    tasks = eval_tasks(seeds[args.weather], args.weather, args.per_kind)
    if args.kinds:
        tasks = [t for t in tasks if t['kind'] in args.kinds.split(',')]
    with ThreadPoolExecutor(args.concurrency) as ex:
        results = list(ex.map(lambda it: fly_one(args, *it), enumerate(tasks)))
    with open(os.path.join(args.out, 'episodes.jsonl'), 'w') as f:
        for r in results:
            f.write(json.dumps(r) + '\n')
    summary = {'model': args.model, 'interface': args.interface, 'reasoning': args.reasoning,
               'realtime': args.realtime,
               'weather': args.weather, 'kinds': {}}
    for kind in REVEAL_KINDS:
        rs = [r for r in results if r['task']['kind'] == kind]
        if not rs:
            continue
        lats = [r['latency_s_median'] for r in rs if r['latency_s_median'] is not None]
        summary['kinds'][kind] = {
            'n': len(rs), 'success': round(sum(r['success'] for r in rs) / len(rs), 4),
            'mean_reward': round(sum(r['reward'] for r in rs) / len(rs), 4),
            'decision_latency_s_median': round(statistics.median(lats), 3) if lats else None,
            'cost_per_flight_usd': round(sum(r['cost_usd'] for r in rs) / len(rs), 6),
            'invalid_actions': sum(r['invalid_actions'] for r in rs),
            'late_intervals': sum(r['late_intervals'] for r in rs),
            'api_errors': sum(bool(r['api_errors']) for r in rs)}
    json.dump(summary, open(os.path.join(args.out, 'summary.json'), 'w'), indent=2)
    print(json.dumps(summary['kinds']))


if __name__ == '__main__':
    main()
