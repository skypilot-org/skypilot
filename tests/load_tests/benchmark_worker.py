#!/usr/bin/env python3
"""Benchmark worker — runs one or more load generators on a worker VM.

Three generator types can run concurrently from a single config file:

  * shell      — bash workload scripts (workloads/basic.sh) emitting
                 ##BENCH_START/END markers
  * qps        — open-loop Python QPS engine
  * long_conn  — concurrent long-lived ssh / sky logs sessions

Usage (config-driven):
    python benchmark_worker.py --config /path/to/bench_config.yaml -w 0

Legacy usage (synthesised single shell generator, kept for backward compat):
    python benchmark_worker.py -t 4 -r 2 -s workloads/basic.sh --cloud aws
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

# Local imports (this file lives in tests/load_tests/, so the package is
# importable as `generators` from this dir).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import BenchmarkConfig  # noqa: E402
from config import load_config
from generators import GeneratorBase  # noqa: E402
from generators import LongConnGenerator
from generators import QpsGenerator
from generators import ShellGenerator
from generators import SshBenchGenerator
from generators.base import WorkerContext  # noqa: E402


def _make_generators(cfg: BenchmarkConfig,
                     ctx: WorkerContext) -> List[GeneratorBase]:
    gens: List[GeneratorBase] = []
    for spec in cfg.generators:
        if spec.type == 'shell':
            gens.append(ShellGenerator(spec, ctx))
        elif spec.type == 'qps':
            gens.append(QpsGenerator(spec, ctx))
        elif spec.type == 'long_conn':
            gens.append(LongConnGenerator(spec, ctx))
        elif spec.type == 'ssh_bench':
            gens.append(SshBenchGenerator(spec, ctx))
        else:
            raise ValueError(f'unknown generator type: {spec.type}')
    return gens


def run_worker(cfg: BenchmarkConfig, worker_id: int, output_dir: str,
               num_workers: int) -> Dict[str, Any]:
    os.makedirs(output_dir, exist_ok=True)
    victims = [
        v for v in os.environ.get('BENCHMARK_VICTIM_CLUSTERS', '').split(',')
        if v
    ]
    ctx = WorkerContext(
        worker_id=worker_id,
        num_workers=num_workers,
        output_dir=output_dir,
        target_cloud=cfg.target.cloud,
        victim_clusters=victims,
        duration_s=cfg.duration_s,
    )
    gens = _make_generators(cfg, ctx)

    print(
        f'[worker {worker_id}] starting {len(gens)} generators '
        f'(victims={victims}, duration={cfg.duration_s}s)',
        flush=True)
    t0 = time.time()
    for g in gens:
        g.start()

    # Termination policy:
    #   - "Driver" generators decide when the worker can exit. Today:
    #       * shell generators (always drivers: the script runs to end)
    #       * ssh_bench generators with finite per-op total_connections
    #         (drive when their attempt budget is exhausted)
    #   - Non-driver generators (qps, long_conn, unbounded ssh_bench)
    #     keep running until ALL drivers finish; duration_s is only a
    #     lower bound in that case.
    #   - If there are no drivers, cfg.duration_s is the fence.
    def _is_driver(g):
        if g.spec.type == 'shell':
            return True
        if g.spec.type == 'ssh_bench' and getattr(g, 'is_bounded', False):
            return True
        return False

    drivers = [g for g in gens if _is_driver(g)]
    non_drivers = [g for g in gens if not _is_driver(g)]

    if drivers:
        for g in drivers:
            g.wait()
        elapsed = time.time() - t0
        remaining = cfg.duration_s - elapsed
        if non_drivers and remaining > 0:
            print(
                f'[worker {worker_id}] drivers done in {elapsed:.1f}s; '
                f'waiting {remaining:.1f}s more to reach duration_s',
                flush=True)
            time.sleep(remaining)
        elif non_drivers:
            print(
                f'[worker {worker_id}] drivers done in {elapsed:.1f}s; '
                f'duration_s already elapsed — stopping',
                flush=True)
    elif non_drivers:
        # No driver — let non-driver generators run for duration_s.
        print(
            f'[worker {worker_id}] no driver generator; '
            f'running non-drivers for {cfg.duration_s}s',
            flush=True)
        time.sleep(cfg.duration_s)

    print(f'[worker {worker_id}] stopping generators', flush=True)
    for g in gens:
        try:
            g.stop()
        except Exception as e:  # noqa: BLE001
            print(f'[worker {worker_id}] gen {g.name} stop error: {e}',
                  file=sys.stderr,
                  flush=True)

    # Assemble combined results.
    shell_records: List[Dict[str, Any]] = []
    generator_results: Dict[str, Any] = {}
    for g in gens:
        recs = g.records()
        summary = g.summarize()
        if g.spec.type == 'shell':
            shell_records.extend(recs)
            generator_results[g.name] = {
                'type': g.spec.type,
                'summary': summary,
            }
        else:
            generator_results[g.name] = {
                'type': g.spec.type,
                'summary': summary,
                'records': recs,
            }

    combined = {
        'worker_id': worker_id,
        'shell_results': shell_records,
        'generator_results': generator_results,
        'wall_clock_s': round(time.time() - t0, 2),
    }
    out_path = os.path.join(output_dir, f'results_w{worker_id}.json')
    with open(out_path, 'w') as f:
        json.dump(combined, f, indent=2)
    print(f'[worker {worker_id}] results written to {out_path}', flush=True)
    return combined


def _print_summary(combined: Dict[str, Any]) -> None:
    print(f'\n{"="*60}')
    print(f'Worker {combined["worker_id"]} summary')
    print(f'{"="*60}')
    for name, gr in combined['generator_results'].items():
        print(f'  [{gr["type"]}] {name}: {json.dumps(gr["summary"])}')
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Benchmark worker: run load generators per config')
    parser.add_argument('--config',
                        type=str,
                        default=None,
                        help='Path to bench config YAML')
    parser.add_argument('-w', '--worker-id', type=int, default=0)
    parser.add_argument('-o',
                        '--output-dir',
                        type=str,
                        default='benchmark_results')
    parser.add_argument('--num-workers',
                        type=int,
                        default=1,
                        help='Total workers in the run (for QPS splitting)')
    # Legacy flags (synth single shell generator).
    parser.add_argument('-t', '--threads', type=int, default=4)
    parser.add_argument('-r', '--repeats', type=int, default=1)
    parser.add_argument('-s',
                        '--script',
                        type=str,
                        default='workloads/basic.sh')
    parser.add_argument('-c', '--cloud', type=str, default='aws')
    parser.add_argument('--timeout', type=int, default=3600)
    parser.add_argument('--phases', type=str, default='cluster,jobs')
    parser.add_argument('--check',
                        action='store_true',
                        help='Exit non-zero on any failure')
    args = parser.parse_args()

    if args.config:
        cfg = load_config(args.config)
    else:
        cfg = load_config(
            None,
            target_endpoint='http://unused-in-worker',  # worker doesn't relogin
            service_account_token=None,
            cloud=args.cloud,
            worker_cloud=args.cloud,
            worker_cpus=8,
            worker_image=None,
            workers_count=args.num_workers,
            threads_per_worker=args.threads,
            repeats=args.repeats,
            workload=args.script,
            phases=args.phases,
            timeout=args.timeout,
            output_dir=args.output_dir,
            reuse=False,
            cleanup=True,
        )
    combined = run_worker(cfg, args.worker_id, args.output_dir,
                          args.num_workers)
    _print_summary(combined)

    if args.check:
        failed = sum(
            1 for r in combined['shell_results'] if not r.get('success'))
        if failed:
            print(f'{failed} runs failed', file=sys.stderr)
            sys.exit(1)


if __name__ == '__main__':
    main()
