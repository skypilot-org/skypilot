#!/usr/bin/env python3
"""Benchmark controller — launches worker VMs and aggregates results.

Two ways to invoke:

1. Config-driven (recommended for mixed-mode runs):

    python benchmark_ctl.py --config configs/mixed.yaml \\
        --target-endpoint http://EKS_LB_URL

   Most knobs (workers, generators, victim pool, duration) come from the
   YAML. CLI flags can still override --target-endpoint, --output-dir,
   --reuse, --no-cleanup, --collect-only.

2. Legacy (single shell workload, backward-compatible):

    python benchmark_ctl.py \\
        --workers 4 --threads-per-worker 4 --repeats 2 \\
        --workload workloads/basic.sh \\
        --target-endpoint http://EKS_LB_URL --cloud aws

For HTTP basic auth, embed credentials in the URL:
    --target-endpoint http://user:pass@host
For service-account auth, pass --service-account-token sky_xxx.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import as_completed
from concurrent.futures import ThreadPoolExecutor
import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

# Local imports.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import BenchmarkConfig  # noqa: E402
from config import load_config
from config import to_yaml
from generators.base import summarize_durations  # noqa: E402

WORKER_PREFIX = 'bench-worker'
REMOTE_BENCH_DIR = '~/sky_workdir'
LOAD_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))

# ── shell helper ─────────────────────────────────────────────────────


def _run(cmd: str,
         check: bool = True,
         capture: bool = False,
         timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    print(f'  $ {cmd}', flush=True)
    return subprocess.run(cmd,
                          shell=True,
                          check=check,
                          text=True,
                          capture_output=capture,
                          timeout=timeout)


def _worker_name(i: int) -> str:
    return f'{WORKER_PREFIX}-{i}'


# ── Phase 1: launch workers ──────────────────────────────────────────

_LAUNCH_TIMEOUT_S = 1800  # AWS provisioning + setup can take 10–15 min


def _cluster_is_up(name: str) -> bool:
    """Check whether a SkyPilot cluster is in UP state."""
    # `sky status <name>` exits non-zero on missing clusters.
    res = _run(f'sky status {name}', check=False, capture=True, timeout=60)
    if res.returncode != 0:
        return False
    out = (res.stdout or '') + '\n' + (res.stderr or '')
    # The status table has a line containing the cluster name followed by
    # columns; look for "UP" on the same line.
    for line in out.splitlines():
        if name in line and ' UP' in line:
            return True
    return False


def _verify_up(names: List[str], wait_s: int = 900) -> List[str]:
    """Return the subset of `names` that are actually UP, waiting up to
    wait_s for stragglers (useful when `sky launch` times out locally but
    the API server keeps provisioning)."""
    deadline = time.time() + wait_s
    remaining = list(names)
    up: List[str] = []
    while remaining and time.time() < deadline:
        still_pending: List[str] = []
        for n in remaining:
            if _cluster_is_up(n):
                up.append(n)
                print(f'  {n}: UP', flush=True)
            else:
                still_pending.append(n)
        remaining = still_pending
        if remaining:
            print(f'  still waiting for: {remaining}', flush=True)
            time.sleep(30)
    for n in remaining:
        print(f'  {n}: NOT UP (giving up)', file=sys.stderr, flush=True)
    return up


def launch_workers(cfg: BenchmarkConfig) -> List[str]:
    n = cfg.workers.count
    print(f'\n=== Launching {n} worker VMs on {cfg.workers.cloud} ===',
          flush=True)
    setup = ('pip install "skypilot-nightly[aws,kubernetes]" '
             '> /dev/null 2>&1')
    target_names = [_worker_name(i) for i in range(n)]

    def _launch_one(wid: int) -> str:
        name = _worker_name(wid)
        cmd = (f'sky launch -y -c {name} --infra {cfg.workers.cloud} '
               f'--cpus {cfg.workers.cpus}+ --memory {cfg.workers.memory}+ '
               f'--workdir {LOAD_TESTS_DIR} '
               f"'{setup}'")
        if cfg.workers.image:
            cmd += f' --image {cfg.workers.image}'
        _run(cmd, timeout=_LAUNCH_TIMEOUT_S, check=False)
        return name

    launched: List[str] = []
    with ThreadPoolExecutor(max_workers=min(n, 8)) as pool:
        futs = {pool.submit(_launch_one, i): i for i in range(n)}
        for fut in as_completed(futs):
            wid = futs[fut]
            try:
                launched.append(fut.result())
            except Exception as e:  # noqa: BLE001
                print(f'  Worker {wid} launch subprocess error: {e}',
                      file=sys.stderr,
                      flush=True)

    # `sky launch` timing out locally doesn't always mean the cluster
    # failed — the API server may still be provisioning. Verify state.
    print('  Verifying worker cluster state ...', flush=True)
    up = _verify_up(target_names, wait_s=900)
    if not up:
        raise RuntimeError(
            f'No workers came UP after launch + 15 min verify window. '
            f'Attempted: {target_names}')
    if len(up) < n:
        print(f'WARNING: only {len(up)}/{n} workers UP ({up})',
              file=sys.stderr,
              flush=True)
    return sorted(up)


# ── Phase 2: setup workers (upload config + login to target) ─────────


def _build_login_cmd(cfg: BenchmarkConfig) -> str:
    cmd = f'sky api login -e {cfg.target.endpoint}'
    if cfg.target.service_account_token:
        cmd += f' --token {cfg.target.service_account_token}'
    return cmd


_SSH_OPTS = ('-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null '
             '-o ConnectTimeout=15 -o ServerAliveInterval=30')


def _scp_up(cluster: str,
            local: str,
            remote: str,
            recursive: bool = False,
            timeout: int = 300) -> None:
    """Upload a file/dir via scp. Uses -O (legacy protocol) because some SkyPilot
    worker images don't have the sftp subsystem enabled in sshd."""
    flag_r = '-r ' if recursive else ''
    cmd = f'scp -O {flag_r}{_SSH_OPTS} {local} {cluster}:{remote}'
    _run(cmd, timeout=timeout)


def _ssh_run(cluster: str,
             remote_cmd: str,
             timeout: int = 300,
             check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command on the cluster via ssh (uses ~/.ssh/config entry)."""
    # Single-quote the command safely.
    quoted = remote_cmd.replace("'", "'\\''")
    cmd = f"ssh {_SSH_OPTS} {cluster} '{quoted}'"
    return _run(cmd, timeout=timeout, check=check)


def _upload_bench_config(name: str, cfg: BenchmarkConfig,
                         victims: List[str]) -> None:
    """Push bench_config.yaml + victims.txt into ~/sky_workdir on the worker.

    The worker's ~/sky_workdir already contains the benchmark code (mounted
    via `sky launch --workdir LOAD_TESTS_DIR`). We only need to drop the
    config and victim-list files on top.
    """
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = os.path.join(tmp, 'bench_config.yaml')
        with open(cfg_path, 'w') as f:
            f.write(to_yaml(cfg))
        victims_path = os.path.join(tmp, 'victims.txt')
        with open(victims_path, 'w') as f:
            f.write('\n'.join(victims))
        # Ensure the target dir exists (it does after --workdir, but be safe).
        _ssh_run(name, 'mkdir -p ~/sky_workdir', timeout=60)
        _scp_up(name, cfg_path, '~/sky_workdir/bench_config.yaml')
        _scp_up(name, victims_path, '~/sky_workdir/victims.txt')


def _reupload_code(name: str) -> None:
    """Re-sync the load_tests/ source to the worker (used with --reuse so local
    edits are picked up without re-launching)."""
    # Use scp -O -r to overwrite everything. Trailing / semantics: copy dir
    # contents not the dir itself.
    _ssh_run(name, 'mkdir -p ~/sky_workdir', timeout=60)
    _scp_up(name,
            f'{LOAD_TESTS_DIR}/.',
            '~/sky_workdir/',
            recursive=True,
            timeout=600)


def setup_workers(names: List[str],
                  cfg: BenchmarkConfig,
                  victims: List[str],
                  reupload: bool = False) -> None:
    print(f'\n=== Setting up {len(names)} workers ===', flush=True)
    login_cmd = _build_login_cmd(cfg)

    def _setup_one(name: str) -> None:
        if reupload:
            _reupload_code(name)
        _upload_bench_config(name, cfg, victims)
        _run(
            f"sky exec -c {name} 'pip install "
            f'"skypilot-nightly[aws,kubernetes]" > /dev/null 2>&1\'',
            timeout=300)
        _run(f"sky exec -c {name} '{login_cmd}'", timeout=60)

    with ThreadPoolExecutor(max_workers=min(len(names), 8)) as pool:
        futs = {pool.submit(_setup_one, n): n for n in names}
        for fut in as_completed(futs):
            n = futs[fut]
            try:
                fut.result()
                print(f'  {n} configured', flush=True)
            except Exception as e:  # noqa: BLE001
                print(f'  {n} setup FAILED: {e}', file=sys.stderr, flush=True)


# ── Phase 2.5: victim pool ───────────────────────────────────────────


def provision_victim_pool(cfg: BenchmarkConfig) -> List[str]:
    if not cfg.victim_pool.enabled:
        return []
    names = cfg.victim_pool.names()
    if not cfg.victim_pool.provision:
        print(
            f'\n=== Skipping victim provisioning (assumed existing): '
            f'{names} ===',
            flush=True)
        return names
    print(
        f'\n=== Provisioning {len(names)} victim clusters on '
        f'{cfg.victim_pool.cloud} ===',
        flush=True)

    # A long-running heartbeat job so logs_follow has something to tail.
    run_cmd = ("'while true; do echo \"victim heartbeat $(date +%s)\"; "
               "sleep 1; done'")

    def _launch_one(name: str) -> str:
        cmd = (f'sky launch -y -c {name} --infra {cfg.victim_pool.cloud} '
               f'--cpus {cfg.victim_pool.cpus}+ '
               f'--memory {cfg.victim_pool.memory}+ '
               f'-d {run_cmd}')
        _run(cmd, timeout=900)
        return name

    with ThreadPoolExecutor(max_workers=min(len(names), 8)) as pool:
        futs = {pool.submit(_launch_one, n): n for n in names}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                fut.result()
                print(f'  victim {name} ready', flush=True)
            except Exception as e:  # noqa: BLE001
                print(f'  victim {name} FAILED: {e}',
                      file=sys.stderr,
                      flush=True)
    return names


def teardown_victim_pool(cfg: BenchmarkConfig) -> None:
    if not cfg.victim_pool.enabled or not cfg.victim_pool.teardown:
        return
    names = cfg.victim_pool.names()
    if not names:
        return
    print(f'\n=== Tearing down {len(names)} victim clusters ===', flush=True)
    for name in names:
        _run(f'sky down {name} -y', check=False, timeout=120)


# ── Phase 3: run benchmark ───────────────────────────────────────────


def run_benchmark(names: List[str], cfg: BenchmarkConfig,
                  victims: List[str]) -> None:
    print(f'\n=== Running benchmark on {len(names)} workers ===', flush=True)
    n_workers = len(names)
    # Outer timeout: shell timeout * repeats + duration_s + slack.
    shell_timeout = max(
        (g.timeout_per_run_s * g.repeats for g in cfg.shell_generators()),
        default=0)
    outer_timeout = shell_timeout + cfg.duration_s + 600

    def _run_one(wid: int, name: str) -> None:
        victims_env = ','.join(victims)
        remote = (f'cd {REMOTE_BENCH_DIR} && '
                  f'BENCHMARK_VICTIM_CLUSTERS={victims_env} '
                  f'python benchmark_worker.py '
                  f'--config {REMOTE_BENCH_DIR}/bench_config.yaml '
                  f'-w {wid} --num-workers {n_workers} '
                  f'-o ~/benchmark_results')
        _run(f"sky exec -c {name} '{remote}'",
             timeout=outer_timeout,
             check=False)

    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        futs = {pool.submit(_run_one, i, n): n for i, n in enumerate(names)}
        for fut in as_completed(futs):
            n = futs[fut]
            try:
                fut.result()
                print(f'  {n} finished', flush=True)
            except Exception as e:  # noqa: BLE001
                print(f'  {n} error: {e}', file=sys.stderr, flush=True)


# ── Phase 4: collect results ─────────────────────────────────────────


def _scp_down(cluster: str,
              remote: str,
              local: str,
              timeout: int = 120) -> bool:
    """Download a remote file via scp (SkyPilot adds clusters to ~/.ssh/config).

    Uses -O (legacy SCP protocol) because some SkyPilot worker images don't
    have the sftp subsystem enabled in sshd, which newer OpenSSH scp defaults
    to and which otherwise fails with "Connection closed".
    Returns True on success.
    """
    cmd = f'scp -O {_SSH_OPTS} {cluster}:{remote} {local}'
    res = _run(cmd, check=False, capture=True, timeout=timeout)
    if res.returncode != 0:
        stderr = (res.stderr or '').strip()
        print(f'  {cluster}: scp failed{": " + stderr if stderr else ""}',
              flush=True)
        return False
    return True


def collect_results(names: List[str], output_dir: str) -> List[Dict[str, Any]]:
    print(f'\n=== Collecting results ===', flush=True)
    os.makedirs(output_dir, exist_ok=True)
    all_results: List[Dict[str, Any]] = []

    for i, name in enumerate(names):
        local_dir = os.path.join(output_dir, f'worker_{i}')
        os.makedirs(local_dir, exist_ok=True)
        local_path = os.path.join(local_dir, f'results_w{i}.json')
        try:
            ok = _scp_down(name, f'~/benchmark_results/results_w{i}.json',
                           local_path)
            if not ok:
                continue
            with open(local_path) as fh:
                data = json.load(fh)
            all_results.append(data)
            shell_n = len(data.get('shell_results', []))
            gen_n = len(data.get('generator_results', {}))
            print(
                f'  {name}: loaded ({shell_n} shell records, '
                f'{gen_n} generators)',
                flush=True)
        except Exception as e:  # noqa: BLE001
            print(f'  {name}: failed to collect: {e}',
                  file=sys.stderr,
                  flush=True)

    merged_path = os.path.join(output_dir, 'all_results.json')
    with open(merged_path, 'w') as f:
        json.dump(all_results, f, indent=2)
    print(f'  Merged {len(all_results)} workers → {merged_path}', flush=True)
    return all_results


# ── Phase 5: report ──────────────────────────────────────────────────


def _print_shell_section(workers: List[Dict[str, Any]]) -> None:
    op_data: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    runs: List[Dict[str, Any]] = []
    for w in workers:
        for r in w.get('shell_results', []):
            runs.append(r)
            for op in r.get('operations', []):
                op_data[op['name']].append(op)
    if not runs:
        return
    total = len(runs)
    ok = sum(1 for r in runs if r.get('success'))
    print(f'\n── Shell workload ──')
    print(f'  runs: {total} ({ok} ok, {total - ok} failed)')
    if op_data:
        print(f'  {"Operation":<25} {"N":>4} {"OK%":>6} '
              f'{"P50":>8} {"P95":>8} {"P99":>8} {"Max":>8}')
        for name in sorted(op_data):
            ops = op_data[name]
            n = len(ops)
            ok_pct = sum(1 for o in ops if o['exit_code'] == 0) / n * 100
            durs = sorted(o['duration_s'] for o in ops)
            p50 = durs[int(n * 0.50)]
            p95 = durs[min(int(n * 0.95), n - 1)]
            p99 = durs[min(int(n * 0.99), n - 1)]
            mx = durs[-1]
            print(f'  {name:<25} {n:>4} {ok_pct:>5.0f}% '
                  f'{p50:>7.1f}s {p95:>7.1f}s {p99:>7.1f}s {mx:>7.1f}s')
    errors = [r for r in runs if not r.get('success')]
    if errors:
        print(f'  errors: {len(errors)}')
        for r in errors[:5]:
            print(f'    worker={r.get("worker_id")} '
                  f'thread={r.get("thread_id")} '
                  f'repeat={r.get("repeat_id")}: '
                  f'{r.get("error", "exit=" + str(r.get("exit_code")))}')


def _print_qps_section(name: str, per_worker: List[Dict[str, Any]]) -> None:
    # per_worker is a list of {'records': [...], 'summary': {...}}
    all_records: List[Dict[str, Any]] = []
    for w in per_worker:
        all_records.extend(w.get('records', []))
    responses = [r for r in all_records if r.get('event') == 'response']
    ok = [r for r in responses if r.get('success')]
    err = [r for r in responses if not r.get('success')]
    throttled = [r for r in all_records if r.get('event') == 'throttled']
    target_qps = per_worker[0]['summary'].get('target_qps_global', 0) \
        if per_worker else 0
    achieved_per_worker = sum(
        w['summary'].get('achieved_qps_per_worker', 0) for w in per_worker)
    print(f'\n── QPS generator: {name} ──')
    print(f'  target QPS (global): {target_qps:.1f}')
    print(f'  achieved QPS (sum of per-worker): {achieved_per_worker:.1f}')
    print(f'  completed: {len(responses)}  errors: {len(err)}  '
          f'throttled: {len(throttled)}')
    by_op: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_op_err: Dict[str, int] = defaultdict(int)
    for r in ok:
        by_op[r['op']].append(r)
    for r in err:
        by_op_err[r['op']] += 1
    if by_op or by_op_err:
        print(f'  {"Op":<14} {"N":>5} {"Err":>5} '
              f'{"P50":>8} {"P95":>8} {"P99":>8}')
        for op in sorted(set(list(by_op) + list(by_op_err))):
            rows = by_op.get(op, [])
            stats = summarize_durations(rows)
            n = stats.get('n', 0)
            print(f'  {op:<14} {n:>5} {by_op_err[op]:>5} '
                  f'{stats.get("p50", 0):>7.3f}s '
                  f'{stats.get("p95", 0):>7.3f}s '
                  f'{stats.get("p99", 0):>7.3f}s')


def _print_long_conn_section(name: str, per_worker: List[Dict[str,
                                                              Any]]) -> None:
    total_slots = sum(w['summary'].get('slots', 0) for w in per_worker)
    connects = sum(w['summary'].get('connects', 0) for w in per_worker)
    discons = sum(w['summary'].get('disconnects', 0) for w in per_worker)
    recons = sum(w['summary'].get('reconnects', 0) for w in per_worker)
    live = sum(w['summary'].get('live_at_summary', 0) for w in per_worker)
    print(f'\n── Long-connection generator: {name} ──')
    print(f'  slots: {total_slots}  connects: {connects}  '
          f'disconnects: {discons}  reconnects: {recons}')
    print(f'  live at summary time: {live}')
    # Aggregate first_data and disconnect duration percentiles.
    all_first = []
    all_sess = []
    for w in per_worker:
        for r in w.get('records', []):
            if r.get('event') == 'first_data' and 'duration_s' in r:
                all_first.append(r)
            if r.get('event') == 'disconnect' and 'duration_s' in r:
                all_sess.append(r)
    if all_first:
        s = summarize_durations(all_first)
        print(f'  time-to-first-log-line: '
              f'p50={s["p50"]:.3f}s p95={s["p95"]:.3f}s '
              f'p99={s["p99"]:.3f}s n={s["n"]}')
    if all_sess:
        s = summarize_durations(all_sess)
        print(f'  session duration:       '
              f'p50={s["p50"]:.1f}s p95={s["p95"]:.1f}s '
              f'p99={s["p99"]:.1f}s n={s["n"]}')


def print_report(workers: List[Dict[str, Any]], cfg: BenchmarkConfig) -> None:
    print(f'\n{"="*72}')
    print(f'BENCHMARK REPORT')
    print(f'{"="*72}')
    print(f'Target:   {cfg.target.endpoint}')
    print(f'Workers:  {cfg.workers.count} on {cfg.workers.cloud}')
    print(
        f'Duration: {cfg.duration_s}s  '
        f'Victims: {cfg.victim_pool.names() if cfg.victim_pool.enabled else "—"}'
    )
    print(f'{"─"*72}')

    if not workers:
        print('No worker results.')
        return

    # Shell section.
    _print_shell_section(workers)

    # Per non-shell generator section.
    by_gen: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_gen_type: Dict[str, str] = {}
    for w in workers:
        for name, gr in (w.get('generator_results') or {}).items():
            by_gen[name].append(gr)
            by_gen_type[name] = gr.get('type', 'unknown')
    for name, per_worker in by_gen.items():
        t = by_gen_type[name]
        if t == 'qps':
            _print_qps_section(name, per_worker)
        elif t == 'long_conn':
            _print_long_conn_section(name, per_worker)

    # Combined footer.
    shell_threads = sum(g.threads_per_worker for g in cfg.shell_generators()) \
        * cfg.workers.count
    qps_total = sum(
        sum(w['summary'].get('achieved_qps_per_worker', 0)
            for w in by_gen[g.name])
        for g in cfg.qps_generators())
    long_conns = sum(
        sum(w['summary'].get('slots', 0)
            for w in by_gen[g.name])
        for g in cfg.long_conn_generators())
    print(f'\n{"─"*72}')
    print(f'Combined pressure: {shell_threads} shell threads + '
          f'{qps_total:.1f} req/s + {long_conns} long connections '
          f'over {cfg.workers.count} workers')
    print()


# ── Phase 6: cleanup ─────────────────────────────────────────────────


def cleanup_workers(names: List[str]) -> None:
    print(f'\n=== Cleaning up {len(names)} workers ===', flush=True)
    for name in names:
        _run(f'sky down {name} -y', check=False, timeout=120)


# ── main ─────────────────────────────────────────────────────────────


def _build_cfg_from_args(args) -> BenchmarkConfig:
    if args.config:
        cfg = load_config(args.config)
        # Allow CLI overrides for a few common knobs.
        if args.target_endpoint:
            cfg.target.endpoint = args.target_endpoint
        if args.service_account_token:
            cfg.target.service_account_token = args.service_account_token
        if args.output_dir:
            cfg.output_dir = args.output_dir
        if args.reuse:
            cfg.workers.reuse = True
        if args.no_cleanup:
            cfg.workers.cleanup = False
        return cfg
    # Legacy synthesised path.
    if not args.target_endpoint:
        raise SystemExit('--target-endpoint or --config is required')
    return load_config(
        None,
        target_endpoint=args.target_endpoint,
        service_account_token=args.service_account_token,
        cloud=args.cloud,
        worker_cloud=args.worker_cloud,
        worker_cpus=args.worker_cpus,
        worker_image=args.worker_image,
        workers_count=args.workers,
        threads_per_worker=args.threads_per_worker,
        repeats=args.repeats,
        workload=args.workload,
        phases=args.phases,
        timeout=args.timeout,
        output_dir=args.output_dir,
        reuse=args.reuse,
        cleanup=not args.no_cleanup,
    )


def main():
    p = argparse.ArgumentParser(
        description='Distributed benchmark controller for SkyPilot API server',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--config',
                   type=str,
                   default=None,
                   help='YAML config file (preferred over individual flags)')
    p.add_argument('--target-endpoint',
                   type=str,
                   default=None,
                   help='API server endpoint URL (basic auth: '
                   'http://user:pass@host)')
    p.add_argument('--service-account-token',
                   type=str,
                   default=None,
                   help='Service-account token (sky_...) for sky api login')
    p.add_argument('--output-dir', type=str, default='bench-results')
    p.add_argument('--reuse',
                   action='store_true',
                   help='Reuse existing worker VMs (skip launch)')
    p.add_argument('--no-cleanup',
                   action='store_true',
                   help='Do not tear down workers after benchmark')
    p.add_argument('--collect-only',
                   action='store_true',
                   help='Only collect & report from existing workers')
    # Legacy single-shell flags.
    p.add_argument('--workers', type=int, default=2)
    p.add_argument('--threads-per-worker', type=int, default=4)
    p.add_argument('--repeats', type=int, default=1)
    p.add_argument('--workload', type=str, default='workloads/basic.sh')
    p.add_argument('--cloud', type=str, default='aws')
    p.add_argument('--worker-cloud', type=str, default='aws')
    p.add_argument('--worker-cpus', type=int, default=8)
    p.add_argument('--worker-image', type=str, default=None)
    p.add_argument('--timeout', type=int, default=3600)
    p.add_argument('--phases', type=str, default='cluster,jobs')
    args = p.parse_args()

    cfg = _build_cfg_from_args(args)
    target_names = [_worker_name(i) for i in range(cfg.workers.count)]
    names: List[str] = []

    try:
        if args.collect_only:
            workers = collect_results(target_names, cfg.output_dir)
            print_report(workers, cfg)
            return

        if cfg.workers.reuse:
            print('  Verifying reused worker cluster state ...', flush=True)
            names = _verify_up(target_names, wait_s=300)
            if not names:
                raise RuntimeError(
                    f'--reuse set but no workers UP: {target_names}')
        else:
            names = launch_workers(cfg)

        victims = provision_victim_pool(cfg) if cfg.victim_pool.enabled else []
        # Setup happens after victim provisioning so victims are passed in.
        setup_workers(names, cfg, victims, reupload=cfg.workers.reuse)
        run_benchmark(names, cfg, victims)
        workers = collect_results(names, cfg.output_dir)
        print_report(workers, cfg)

    except KeyboardInterrupt:
        print('\nInterrupted by user', flush=True)
    finally:
        try:
            teardown_victim_pool(cfg)
        except Exception as e:  # noqa: BLE001
            print(f'victim teardown error: {e}', file=sys.stderr, flush=True)
        if cfg.workers.cleanup and not cfg.workers.reuse:
            # Tear down every target name, not just the ones that came UP —
            # partially-provisioned clusters also need cleaning.
            cleanup_workers(target_names)


if __name__ == '__main__':
    main()
