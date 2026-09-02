"""Run a CPU-only RL-shaped workload on topology-aware Ray actors."""

import argparse
import collections
import json
import os
import socket
import tempfile
import time
from typing import Any, Dict, List
import uuid

import ray
from ray.util.placement_group import get_placement_group
from ray.util.placement_group import placement_group
from ray.util.scheduling_strategies import PlacementGroupSchedulingStrategy


@ray.remote(max_restarts=-1, max_task_retries=-1)
class RolloutWorker:
    """A recoverable actor that reserves one four-GPU Ray bundle."""

    def __init__(self, rank: int):
        self.rank = rank
        self.incarnation = uuid.uuid4().hex[:8]
        self.host = socket.gethostname()
        self.pid = os.getpid()
        self.node_id = str(ray.get_runtime_context().get_node_id())
        self.clique = os.environ.get('NVIDIA_GPU_CLIQUE', 'unknown')
        self.sky_rank = os.environ.get('SKYPILOT_NODE_RANK', 'unknown')
        print(json.dumps(self._event('actor_started')), flush=True)

    def _event(self, event: str, **fields: Any) -> Dict[str, Any]:
        return {
            'event': event,
            'time': time.time(),
            'rank': self.rank,
            'incarnation': self.incarnation,
            'host': self.host,
            'pid': self.pid,
            'node_id': self.node_id,
            'clique': self.clique,
            'skypilot_node_rank': self.sky_rank,
            **fields,
        }

    def identity(self) -> Dict[str, Any]:
        return self._event('identity')

    def rollout(self, step: int, duration_seconds: int,
                heartbeat_seconds: int) -> Dict[str, Any]:
        """Execute an idempotent long-running rollout with progress logs."""
        call_id = uuid.uuid4().hex[:8]
        started = time.monotonic()
        print(
            json.dumps(
                self._event('rollout_started', step=step, call_id=call_id)),
            flush=True,
        )
        while True:
            elapsed = time.monotonic() - started
            if elapsed >= duration_seconds:
                break
            print(
                json.dumps(
                    self._event(
                        'rollout_progress',
                        step=step,
                        call_id=call_id,
                        elapsed_seconds=round(elapsed, 1),
                    )),
                flush=True,
            )
            time.sleep(min(heartbeat_seconds, duration_seconds - elapsed))
        result = self._event(
            'rollout_completed',
            step=step,
            call_id=call_id,
            elapsed_seconds=round(time.monotonic() - started, 1),
        )
        print(json.dumps(result), flush=True)
        return result


def load_state(path: str) -> Dict[str, int]:
    if not os.path.exists(path):
        return {'last_completed_step': -1}
    with open(path, encoding='utf-8') as state_file:
        return json.load(state_file)


def save_state(path: str, state: Dict[str, int]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(dir=os.path.dirname(path),
                                          prefix='.driver-state-',
                                          text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as state_file:
            json.dump(state, state_file)
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def wait_for_cluster_gpus(total_gpus: int, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resources = ray.cluster_resources()
        connected_gpus = int(resources.get('GPU', 0))
        if connected_gpus >= total_gpus:
            print(f'Ray cluster has {connected_gpus} GPUs: {resources}',
                  flush=True)
            return
        print(f'Waiting for {total_gpus} GPUs; resources={resources}',
              flush=True)
        time.sleep(5)
    raise TimeoutError(
        f'Ray did not register {total_gpus} GPUs within {timeout}s')


def get_or_create_placement_group(
    name: str,
    workers_per_clique: int,
    gpus_per_worker: int,
    timeout: int,
    allow_create: bool,
):
    try:
        group = get_placement_group(name)
        print(f'Reattached to detached placement group {name}', flush=True)
    except ValueError as error:
        if not allow_create:
            raise RuntimeError(
                f'Detached placement group {name!r} is missing while '
                'resuming from a completed step') from error
        group = placement_group(  # pylint: disable=unexpected-keyword-arg
            bundles=[{
                'CPU': 1,
                'GPU': gpus_per_worker,
            }] * workers_per_clique,
            name=name,
            lifetime='detached',
            topology_strategy={
                'ray.io/node-id': 'PACK',
                'ray.io/gpu-domain': 'STRICT_PACK',
            },
        )
        print(f'Created detached placement group {name}', flush=True)
    ray.get(group.ready(), timeout=timeout)
    print(f'Placement group ready: id={group.id.hex()} name={name}', flush=True)
    return group


def get_or_create_placement_groups(
    name_prefix: str,
    num_cliques: int,
    workers_per_clique: int,
    gpus_per_worker: int,
    timeout: int,
    allow_create: bool,
) -> List[Any]:
    return [
        get_or_create_placement_group(
            name=f'{name_prefix}-clique-{clique_index}',
            workers_per_clique=workers_per_clique,
            gpus_per_worker=gpus_per_worker,
            timeout=timeout,
            allow_create=allow_create,
        ) for clique_index in range(num_cliques)
    ]


def get_or_create_workers(
    groups: List[Any],
    workers_per_clique: int,
    gpus_per_worker: int,
    allow_create: bool,
) -> List[ray.actor.ActorHandle]:
    workers = []
    num_workers = len(groups) * workers_per_clique
    for rank in range(num_workers):
        name = f'nvl72-rollout-worker-{rank}'
        try:
            worker = ray.get_actor(name)
            print(f'Reattached to detached actor {name}', flush=True)
        except ValueError as error:
            if not allow_create:
                raise RuntimeError(
                    f'Detached actor {name!r} is missing while resuming '
                    'from a completed step') from error
            clique_index = rank // workers_per_clique
            bundle_index = rank % workers_per_clique
            strategy = PlacementGroupSchedulingStrategy(
                placement_group=groups[clique_index],
                placement_group_bundle_index=bundle_index,
                placement_group_capture_child_tasks=True,
            )
            worker = RolloutWorker.options(
                name=name,
                lifetime='detached',
                num_cpus=1,
                num_gpus=gpus_per_worker,
                scheduling_strategy=strategy,
            ).remote(rank)
            print(f'Created detached actor {name}', flush=True)
        workers.append(worker)
    return workers


def get_with_recovery_timeout(object_refs: List[ray.ObjectRef],
                              timeout: int) -> List[Dict[str, Any]]:
    deadline = time.monotonic() + timeout
    remaining = list(object_refs)
    results: List[Dict[str, Any]] = []
    while remaining:
        time_left = deadline - time.monotonic()
        if time_left <= 0:
            raise TimeoutError(
                f'{len(remaining)} Ray rollout(s) did not recover within '
                f'{timeout}s')
        ready, remaining = ray.wait(remaining,
                                    num_returns=1,
                                    timeout=min(10, time_left))
        if ready:
            results.extend(ray.get(ready))
        else:
            alive_nodes = sum(1 for node in ray.nodes() if node['Alive'])
            print(
                f'Waiting for {len(remaining)} rollout(s); '
                f'alive_nodes={alive_nodes}; '
                f'resources={ray.cluster_resources()}',
                flush=True,
            )
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--address', required=True)
    parser.add_argument('--num-cliques', required=True, type=int)
    parser.add_argument('--workers-per-clique', required=True, type=int)
    parser.add_argument('--gpus-per-worker', required=True, type=int)
    parser.add_argument('--steps', required=True, type=int)
    parser.add_argument('--rollout-seconds', required=True, type=int)
    parser.add_argument('--heartbeat-seconds', required=True, type=int)
    parser.add_argument('--recovery-timeout', required=True, type=int)
    parser.add_argument('--state-path', required=True)
    args = parser.parse_args()

    managed_job_id = os.environ['SKYPILOT_MANAGED_JOB_ID']
    namespace = f'ray-nvl72-training-{managed_job_id}'
    ray.init(address=args.address, namespace=namespace)

    state = load_state(args.state_path)
    allow_create = int(state['last_completed_step']) == -1
    num_workers = args.num_cliques * args.workers_per_clique
    wait_for_cluster_gpus(num_workers * args.gpus_per_worker,
                          args.recovery_timeout)
    groups = get_or_create_placement_groups(
        name_prefix=f'nvl72-trainers-{managed_job_id}',
        num_cliques=args.num_cliques,
        workers_per_clique=args.workers_per_clique,
        gpus_per_worker=args.gpus_per_worker,
        timeout=args.recovery_timeout,
        allow_create=allow_create,
    )
    workers = get_or_create_workers(
        groups,
        args.workers_per_clique,
        args.gpus_per_worker,
        allow_create=allow_create,
    )
    identities = ray.get([worker.identity.remote() for worker in workers])
    workers_by_clique = collections.Counter(
        identity['clique'] for identity in identities)
    if len(workers_by_clique) != args.num_cliques or any(
            count != args.workers_per_clique
            for count in workers_by_clique.values()):
        raise RuntimeError('Unexpected Ray placement across GPU cliques: '
                           f'expected {args.num_cliques} clique(s) with '
                           f'{args.workers_per_clique} worker(s) each, got '
                           f'{dict(workers_by_clique)}')
    print(
        json.dumps({
            'event': 'initial_placement',
            'workers': sorted(identities, key=lambda item: item['rank']),
        }),
        flush=True,
    )

    first_step = int(state['last_completed_step']) + 1
    print(f'Starting at step {first_step}; state={state}', flush=True)
    for step in range(first_step, args.steps):
        refs = [
            worker.rollout.remote(step, args.rollout_seconds,
                                  args.heartbeat_seconds) for worker in workers
        ]
        results = get_with_recovery_timeout(refs, args.recovery_timeout)
        results.sort(key=lambda result: result['rank'])
        state = {'last_completed_step': step}
        save_state(args.state_path, state)
        print(
            json.dumps({
                'event': 'step_completed',
                'step': step,
                'workers': results
            }),
            flush=True,
        )


if __name__ == '__main__':
    main()
