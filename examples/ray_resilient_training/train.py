"""Run an RL-style multi-node GPU workload with recoverable Ray actors."""

import argparse
import json
import os
import socket
import statistics
import tempfile
import time
from typing import Any, Dict, List
import uuid

import ray


@ray.remote(num_cpus=1, num_gpus=1, max_restarts=-1, max_task_retries=-1)
class GPUWorker:
    """A stateless rollout worker that Ray can reconstruct on another node."""

    def __init__(self, rank: int, matrix_size: int):
        import torch  # pylint: disable=import-outside-toplevel

        if not torch.cuda.is_available():
            raise RuntimeError('Ray assigned a GPU but PyTorch cannot use CUDA')

        self.rank = rank
        self.matrix_size = matrix_size
        self.torch = torch
        self.device = torch.device('cuda:0')
        generator = torch.Generator(device=self.device)
        generator.manual_seed(rank)
        self.weight = torch.randn((matrix_size, matrix_size),
                                  generator=generator,
                                  device=self.device)
        self.incarnation = uuid.uuid4().hex[:8]

        print(
            f'GPU worker rank={rank} incarnation={self.incarnation} '
            f'host={socket.gethostname()} pid={os.getpid()} '
            f'node_id={ray.get_runtime_context().get_node_id()} '
            f'gpu={torch.cuda.get_device_name(0)}',
            flush=True,
        )

    def rollout(self, step: int, num_batches: int) -> Dict[str, Any]:
        """Run an idempotent synthetic rollout for one policy step."""
        generator = self.torch.Generator(device=self.device)
        generator.manual_seed(step * 10_000 + self.rank)
        activations = self.torch.randn(
            (self.matrix_size, self.matrix_size),
            generator=generator,
            device=self.device,
        )

        started_at = time.monotonic()
        for _ in range(num_batches):
            activations = self.torch.tanh(activations @ self.weight)
            self.torch.cuda.synchronize()
        elapsed_seconds = time.monotonic() - started_at

        return {
            'rank': self.rank,
            'step': step,
            'reward': float(activations.square().mean().item()),
            'batches': num_batches,
            'elapsed_seconds': elapsed_seconds,
            'incarnation': self.incarnation,
            'host': socket.gethostname(),
            'pid': os.getpid(),
            'node_id': str(ray.get_runtime_context().get_node_id()),
            'cuda_visible_devices': os.environ.get('CUDA_VISIBLE_DEVICES'),
        }


def load_state(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {'last_completed_step': -1, 'policy_version': 0.0}
    with open(path, encoding='utf-8') as state_file:
        return json.load(state_file)


def save_state(path: str, state: Dict[str, Any]) -> None:
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


def wait_for_cluster_gpus(num_workers: int, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resources = ray.cluster_resources()
        connected_gpus = int(resources.get('GPU', 0))
        if connected_gpus >= num_workers:
            print(f'Ray cluster has {connected_gpus} GPUs: {resources}',
                  flush=True)
            return
        print(
            f'Waiting for {num_workers} GPUs; Ray currently reports '
            f'{connected_gpus}: {resources}',
            flush=True,
        )
        time.sleep(5)
    raise TimeoutError(
        f'Ray did not register {num_workers} GPUs within {timeout}s')


def get_or_create_workers(num_workers: int, matrix_size: int,
                          allow_create: bool) -> List[ray.actor.ActorHandle]:
    workers = []
    for rank in range(num_workers):
        name = f'gpu-worker-{rank}'
        try:
            worker = ray.get_actor(name)
            print(f'Reattached to detached actor {name}', flush=True)
        except ValueError as error:
            if not allow_create:
                raise RuntimeError(
                    f'Detached actor {name} is missing while resuming from '
                    'a completed step') from error
            worker = GPUWorker.options(name=name, lifetime='detached').remote(
                rank, matrix_size)
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
        ready, remaining = ray.wait(
            remaining,
            num_returns=1,
            timeout=min(10, time_left),
        )
        if ready:
            results.extend(ray.get(ready))
        else:
            resources = ray.cluster_resources()
            alive_nodes = sum(1 for node in ray.nodes() if node['Alive'])
            print(
                f'Waiting for {len(remaining)} rollout(s); '
                f'alive_nodes={alive_nodes}, resources={resources}',
                flush=True,
            )
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--address', required=True)
    parser.add_argument('--num-workers', required=True, type=int)
    parser.add_argument('--steps', default=1000, type=int)
    parser.add_argument('--batches-per-step', default=5000, type=int)
    parser.add_argument('--matrix-size', default=2048, type=int)
    parser.add_argument('--recovery-timeout', default=900, type=int)
    parser.add_argument('--state-path', required=True)
    args = parser.parse_args()

    managed_job_id = os.environ['SKYPILOT_MANAGED_JOB_ID']
    namespace = f'ray-resilient-training-{managed_job_id}'
    ray.init(address=args.address, namespace=namespace)

    state = load_state(args.state_path)
    wait_for_cluster_gpus(args.num_workers, args.recovery_timeout)
    workers = get_or_create_workers(
        args.num_workers,
        args.matrix_size,
        allow_create=int(state['last_completed_step']) == -1,
    )
    first_step = int(state['last_completed_step']) + 1
    print(f'Starting at step {first_step}; state={state}', flush=True)

    for step in range(first_step, args.steps):
        refs = [
            worker.rollout.remote(step, args.batches_per_step)
            for worker in workers
        ]
        results = get_with_recovery_timeout(refs, args.recovery_timeout)
        results.sort(key=lambda result: result['rank'])

        mean_reward = statistics.fmean(result['reward'] for result in results)
        state = {
            'last_completed_step': step,
            'policy_version': float(state['policy_version']) + mean_reward,
        }
        save_state(args.state_path, state)
        print(
            json.dumps(
                {
                    'step': step,
                    'mean_reward': mean_reward,
                    'policy_version': state['policy_version'],
                    'workers': results,
                },
                sort_keys=True,
            ),
            flush=True,
        )

    print(f'Completed {args.steps} steps; final state={state}', flush=True)


if __name__ == '__main__':
    main()
