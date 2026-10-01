"""Two-cluster RL rollout driver: CPU sandboxes + a GPU model server.

A minimal coding-agent RL inner loop split across two Kubernetes clusters:

  - a model server (any OpenAI-compatible endpoint, e.g. vLLM serving a coder
    model on GPUs) runs on a GPU cluster and is reached over HTTP at --head-url;
  - rollout sandboxes run on a (cheap, CPU) sandbox cluster and dial the model
    server to fix a bug; each diff is graded in a fresh "anti-cheat" eval
    sandbox that starts clean, so only the model-produced diff affects reward;
  - rollouts fan out concurrently, so this also exercises sandbox-cluster
    autoscaling as --num-sandboxes grows.

Files in this example:
  - run_split_cluster.py  : this driver
  - skypilot_backend.py   : SkyPilotSandbox (vime Sandbox protocol over sky.sandbox)
  - agent_openai.py       : the agent that runs inside each rollout sandbox
  - gpu_model_server.yaml : example vLLM serving job for the GPU cluster
  - expose_model_server.sh: exposes the model server via a LoadBalancer

Bring up the model server (see gpu_model_server.yaml + expose_model_server.sh)
and pass its `/v1` URL:

    python run_split_cluster.py \\
        --head-url http://<model-server-ip>:8000/v1 \\
        --model my-model \\
        --sandbox-context <cpu-cluster-context> \\
        --num-sandboxes 8

Requires the SkyPilot sandbox feature (the ``sky.sandbox`` SDK) and a SkyPilot
API server reachable by the SDK.
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import os
import secrets
import textwrap
import time
from typing import Optional, Tuple

try:
    import sky.sandbox as sandbox  # public SDK: ``import sky; sky.sandbox...``
except ImportError:
    import sky_sandbox as sandbox

from skypilot_backend import SkyPilotSandbox

RID = secrets.token_hex(3)
IMAGE = 'python:3.12'
WORKDIR = '/home/agent/repo'
AGENT = 'agent'
_HERE = os.path.dirname(os.path.abspath(__file__))

# --- the task ---------------------------------------------------------------
# A buggy add(); the test expects real addition. The canonical test is
# authoritative and is re-laid-down fresh in the eval sandbox, so the agent
# cannot win by editing the test.
BUGGY_CALC = textwrap.dedent('''\
    def add(a, b):
        # BUG: subtracts instead of adding.
        return a - b


    def mul(a, b):
        return a * b
    ''')

CANONICAL_TEST = textwrap.dedent('''\
    from calc import add, mul


    def test_add():
        assert add(2, 3) == 5
        assert add(-1, 1) == 0
        assert add(0, 0) == 0


    def test_mul():
        assert mul(2, 3) == 6
    ''')

PROBLEM_STATEMENT = textwrap.dedent('''\
    The `add` function in `calc.py` returns the wrong result: it subtracts
    instead of adding. Fix `calc.py` so that `add(a, b)` returns the sum of its
    arguments and the test suite in `test_calc.py` passes. Only edit `calc.py`.
    ''')


def log(msg: str) -> None:
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


# --- task helpers -----------------------------------------------------------


async def ensure_agent_user(sb: SkyPilotSandbox, workdir: str) -> None:
    await sb.exec(
        f'id -u {AGENT} >/dev/null 2>&1 || '
        f'useradd -m -d /home/{AGENT} -s /bin/bash {AGENT}; '
        f'mkdir -p {workdir}; chown -R {AGENT}:{AGENT} /home/{AGENT} {workdir}',
        user='root',
        timeout=60,
        check=True)


async def setup_repo(sb: SkyPilotSandbox, workdir: str) -> None:
    log(f'laying down buggy repo at {workdir}...')
    await sb.write_file(f'{workdir}/calc.py', BUGGY_CALC, user=AGENT)
    await sb.write_file(f'{workdir}/test_calc.py', CANONICAL_TEST, user=AGENT)
    await sb.write_file(f'{workdir}/PROBLEM_STATEMENT.md',
                        PROBLEM_STATEMENT,
                        user=AGENT)
    await sb.exec(
        f'cd {workdir} && git init -q && git config user.email a@e.co && '
        'git config user.name a && git add -A && git commit -q -m initial',
        user=AGENT,
        timeout=60,
        check=True)


async def bootstrap_agent_tools(sb: SkyPilotSandbox) -> None:
    # git for diffing, openai for the agent loop. Public egress covers apt+pypi.
    await sb.exec(
        'export DEBIAN_FRONTEND=noninteractive && apt-get update -qq && '
        'apt-get install -y -qq git >/dev/null && pip install -q openai >/dev/null',
        user='root',
        timeout=300,
        check=True)


async def run_model_agent(sb: SkyPilotSandbox, *, head_url: str,
                          model: str) -> int:
    # Ship the in-sandbox agent into the pod and run it against the model server.
    with open(os.path.join(_HERE, 'agent_openai.py')) as f:
        agent_src = f.read()
    await sb.write_file(f'/home/{AGENT}/agent_openai.py', agent_src, user=AGENT)
    ec, out, err = await sb.exec(
        f'cd {WORKDIR} && python /home/{AGENT}/agent_openai.py '
        f'--base-url {head_url} --model {model} --workdir {WORKDIR} 2>&1',
        user=AGENT,
        timeout=300)
    for line in (out or err).strip().splitlines()[-4:]:
        print('      | ' + line, flush=True)
    return ec


async def git_diff(sb: SkyPilotSandbox, workdir: str) -> str:
    # Only the proposed source fix; exclude the test so the agent can't cheat,
    # and avoid byte-compiled files that break `git apply` downstream.
    _, out, _ = await sb.exec(
        f"cd {workdir} && git add -N . && "
        "git diff -- '*.py' ':(exclude)test_calc.py'",
        user=AGENT,
        timeout=60)
    return out


async def evaluate(diff_text: str, *, image: str,
                   context: Optional[str]) -> Tuple[float, bool, bool]:
    """Grade the diff in a fresh sandbox. Returns (reward, solved, applied)."""
    name = f'vime-eval-{RID}-{secrets.token_hex(2)}'
    async with SkyPilotSandbox(image=image,
                               name=name,
                               context_name=context,
                               timeout=1800) as ev:
        await ensure_agent_user(ev, WORKDIR)
        await ev.write_file(f'{WORKDIR}/calc.py', BUGGY_CALC, user=AGENT)
        await ev.write_file(f'{WORKDIR}/test_calc.py',
                            CANONICAL_TEST,
                            user=AGENT)
        await ev.exec(
            f'cd {WORKDIR} && git init -q && git config user.email e@e.co && '
            'git config user.name e && git add -A && git commit -q -m base',
            user=AGENT,
            timeout=60,
            check=True)
        if not diff_text.strip():
            return 0.0, False, True
        await ev.write_file(f'{WORKDIR}/model.patch', diff_text, user=AGENT)
        ec, _, _ = await ev.exec(
            f'cd {WORKDIR} && git apply --whitespace=nowarn model.patch',
            user=AGENT,
            timeout=60)
        if ec != 0:
            return 0.0, False, False
        await ev.exec('pip install -q pytest >/dev/null 2>&1',
                      user='root',
                      timeout=300,
                      check=True)
        ec, _, _ = await ev.exec(f'cd {WORKDIR} && python -m pytest -q',
                                 user=AGENT,
                                 timeout=300)
        return (1.0 if ec == 0 else 0.0), ec == 0, True


async def one_rollout(idx: int, *, head_url: str, model: str,
                      sandbox_context: Optional[str], image: str) -> dict:
    name = f'vime-roll-{RID}-{idx:03d}'
    t0 = time.time()
    rec = {'name': name, 'reward': 0.0, 'solved': False, 'error': None}
    try:
        async with SkyPilotSandbox(image=image,
                                   name=name,
                                   context_name=sandbox_context,
                                   timeout=1800) as sb:
            await ensure_agent_user(sb, WORKDIR)
            await setup_repo(sb, WORKDIR)
            await bootstrap_agent_tools(sb)
            await run_model_agent(sb, head_url=head_url, model=model)
            diff = await git_diff(sb, WORKDIR)
        reward, solved, _ = await evaluate(diff,
                                           image=image,
                                           context=sandbox_context)
        rec.update(reward=reward,
                   solved=solved,
                   secs=round(time.time() - t0, 1))
        log(f'{name}: REWARD={reward} solved={solved} ({rec["secs"]}s)')
    except Exception as e:  # noqa: BLE001 - isolate per-rollout failures
        rec['error'] = f'{type(e).__name__}: {e}'
        log(f'{name}: FAILED {rec["error"]}')
    return rec


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--head-url',
                    required=True,
                    help='OpenAI-compatible model server, e.g. '
                    'http://<ip>:8000/v1')
    ap.add_argument('--model', default='model')
    ap.add_argument('--num-sandboxes', type=int, default=1)
    ap.add_argument('--sandbox-context',
                    default=None,
                    help='Kubernetes context for the rollout sandboxes '
                    '(default: current kubeconfig context)')
    ap.add_argument('--image', default=IMAGE)
    args = ap.parse_args()

    # The SDK's async create/exec wrap a sync client via asyncio.to_thread,
    # whose default executor is ~cpu+4 workers; widen it so the rollouts fan
    # out at once rather than in small waves.
    asyncio.get_running_loop().set_default_executor(
        concurrent.futures.ThreadPoolExecutor(
            max_workers=max(64, args.num_sandboxes * 3)))

    t0 = time.time()
    log(f'launching {args.num_sandboxes} rollout(s) against {args.head_url} '
        f'(sandbox context: {args.sandbox_context or "current"})')
    try:
        results = await asyncio.gather(
            *(one_rollout(i,
                          head_url=args.head_url,
                          model=args.model,
                          sandbox_context=args.sandbox_context,
                          image=args.image) for i in range(args.num_sandboxes)))
    finally:
        await sandbox.aclose()

    solved = sum(1 for r in results if r.get('solved'))
    rewards = [r['reward'] for r in results]
    errors = [r for r in results if r.get('error')]
    log('=' * 60)
    log(f'rollouts={len(results)}  solved={solved}  '
        f'mean_reward={sum(rewards) / max(1, len(rewards)):.3f}  '
        f'errors={len(errors)}  wall={time.time() - t0:.1f}s')
    for r in errors:
        log(f'  ERROR {r["name"]}: {r["error"]}')
    log('=' * 60)


if __name__ == '__main__':
    asyncio.run(main())
