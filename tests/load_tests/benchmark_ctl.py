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
import base64
import binascii
from collections import defaultdict
from concurrent.futures import as_completed
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
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
         timeout: Optional[int] = None,
         env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    print(f'  $ {cmd}', flush=True)
    return subprocess.run(cmd,
                          shell=True,
                          check=check,
                          text=True,
                          capture_output=capture,
                          timeout=timeout,
                          env=env)


def _target_env(cfg: BenchmarkConfig) -> Dict[str, str]:
    """Env that makes a local sky subprocess talk to the TARGET API server
    (not whichever endpoint the controller is logged into)."""
    env = os.environ.copy()
    env['SKYPILOT_API_SERVER_ENDPOINT'] = cfg.target.endpoint
    return env


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
        parts = line.split()
        if parts and parts[0] == name and 'UP' in parts:
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
    if len(up) < n:
        failed = sorted(set(target_names) - set(up))
        raise RuntimeError(
            f'{len(failed)}/{n} workers failed to come UP: {failed}. '
            f'Aborting benchmark.')
    return sorted(up)


# ── Phase 2: (no-op — setup is merged into the single sky exec per worker)

# Direct ssh/scp from the local machine does NOT work when the local `sky` is
# logged into a remote API server: that server owns the AWS cluster keys,
# not the local user. So we do *everything* through `sky exec`, which the
# local CLI correctly proxies to the API server that has the keys.


def _build_login_cmd(cfg: BenchmarkConfig) -> str:
    cmd = f'sky api login -e {cfg.target.endpoint}'
    if cfg.target.service_account_token:
        cmd += f' --token {cfg.target.service_account_token}'
    return cmd


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
        f'{cfg.victim_pool.cloud} (via target API server) ===',
        flush=True)

    # A long-running heartbeat job so logs_follow has something to tail.
    run_cmd = ("'while true; do echo \"victim heartbeat $(date +%s)\"; "
               "sleep 1; done'")
    # IMPORTANT: victims must be provisioned on the TARGET API server (not
    # on whichever endpoint the controller's local sky is logged into),
    # because workers log into the target and need to be able to reach
    # the victims through it (both for `sky logs --follow` and for raw
    # ssh, whose SSH config entries are populated via that same target).
    env = _target_env(cfg)

    def _launch_one(name: str) -> str:
        cmd = (f'sky launch -y -c {name} --infra {cfg.victim_pool.cloud} '
               f'--cpus {cfg.victim_pool.cpus}+ '
               f'--memory {cfg.victim_pool.memory}+ '
               f'-d {run_cmd}')
        _run(cmd, timeout=900, env=env)
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
    env = _target_env(cfg)
    for name in names:
        _run(f'sky down {name} -y', check=False, timeout=600, env=env)


# ── Phase 3: run benchmark (single sky exec per worker) ──────────────

_RESULT_START = '##RESULT_FILE_START##'
_RESULT_END = '##RESULT_FILE_END##'
# sky exec prefixes user-run stdout with a tag like `(task, pid=NNNN) ` or
# `(sky-cmd, pid=NNNN) ` (the label has varied across SkyPilot versions).
_PREFIX_RE = re.compile(r'^\([A-Za-z0-9_.-]+,\s*pid=\d+\)\s?')
# strip terminal color codes
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')


def _build_worker_bash(cfg: BenchmarkConfig, worker_id: int, n_workers: int,
                       victims: List[str], login_cmd: str) -> str:
    """Build the bash script that runs on a worker via sky exec.

    Base64-encodes bench_config.yaml inline (heredoc delimiters don't play
    well with YAML `run: |` indentation). Prints result JSON with sentinel
    markers so the controller can extract it from captured stdout.
    """
    cfg_b64 = base64.b64encode(to_yaml(cfg).encode()).decode()
    victims_str = ','.join(victims)
    # Prime ~/.ssh/config for each victim. Only `sky status <name>`
    # populates the client-side SSH config: it calls the server with
    # _include_credentials=True and then runs SSHConfigHelper.add_cluster,
    # which for Kubernetes clusters writes a ProxyCommand that tunnels
    # SSH through the API server's /kubernetes-pod-ssh-proxy websocket
    # endpoint (see sky/client/cli/command.py:_get_cluster_records_and_set_ssh_config).
    # `sky exec` does NOT populate the worker's local ssh_config when the
    # API server is remote, because the server is the one doing SSH in
    # that flow — the client just submits a task over HTTP.
    prime_lines = '\n        '.join(
        f'sky status {v} >/dev/null 2>&1 || '
        f'echo "[warn] prime ssh config for {v} failed"' for v in victims)
    prime_block = prime_lines if victims else 'true  # no victims to prime'
    # Optionally install the SkyPilot Go CLI on the worker and prepend its
    # bin dir to PATH so all subsequent `sky` invocations (login, status,
    # workload scripts) use the Go binary. The Python SDK install above is
    # still needed for generators that do `import sky`.
    if cfg.workers.use_go_client:
        version_arg = (f' -s -- --version {cfg.workers.go_client_version}'
                       if cfg.workers.go_client_version else '')
        go_install_block = (
            'curl -fsSL '
            'https://skypilot-cli.s3.us-west-2.amazonaws.com/install.sh '
            f'| bash{version_arg}\n        '
            'export PATH="$HOME/.sky/bin:$PATH"\n        '
            'echo "[go-client] using $(command -v sky) "'
            '"($(sky --version 2>&1 | head -1))"')
    else:
        go_install_block = 'true  # go client disabled'
    result_file = f'~/benchmark_results/results_w{worker_id}.json'
    # NOTE: no `set -e`. We intentionally let every step proceed so that
    # even if an intermediate step fails, the result markers are still
    # emitted — and if the result file is missing we emit a stub JSON
    # object telling the controller what exit code Python produced.
    #
    # The result is gzipped + base64-encoded before printing. sky exec's
    # log stream is NOT a clean text channel — when result JSON is large
    # (>~500KB) it can drop bytes, inject carriage-returns from progress
    # bars, or inconsistently apply the `(task, pid=N) ` line prefix.
    # Base64 is opaque to all of that; gzip keeps the on-the-wire size
    # small.
    return textwrap.dedent(f'''
        mkdir -p ~/sky_workdir ~/benchmark_results
        echo {cfg_b64} | base64 -d > ~/sky_workdir/bench_config.yaml
        python -c "import sky" 2>/dev/null || \\
            pip install "skypilot-nightly[aws,kubernetes]" >/dev/null 2>&1
        {go_install_block}
        {login_cmd}
        # Prime SSH config for each victim (needed for raw-ssh long connections).
        {prime_block}
        cd ~/sky_workdir
        BENCHMARK_VICTIM_CLUSTERS={victims_str} python benchmark_worker.py \\
            --config ~/sky_workdir/bench_config.yaml \\
            -w {worker_id} --num-workers {n_workers} \\
            -o ~/benchmark_results
        py_rc=$?
        echo ""
        echo "{_RESULT_START}"
        if [ -f {result_file} ]; then
            gzip -c {result_file} | base64 -w 0
        else
            printf '{{"worker_id": {worker_id}, "error": "results file missing", "py_rc": %d, "shell_results": [], "generator_results": {{}}}}' "$py_rc" | gzip -c | base64 -w 0
        fi
        echo ""
        echo "{_RESULT_END}"
    ''').lstrip()


def _write_exec_task_yaml(bash_script: str) -> str:
    """Wrap the bash script in a sky task YAML (`run: |` block) and write to
    a temp file. Caller is responsible for deleting."""
    lines = bash_script.splitlines()
    indented = ''.join(f'  {ln}\n' for ln in lines)
    content = f'run: |\n{indented}'
    fd, path = tempfile.mkstemp(suffix='.yaml', prefix='bench_run_')
    with os.fdopen(fd, 'w') as f:
        f.write(content)
    return path


def _extract_between(text: str, start_marker: str,
                     end_marker: str) -> Optional[str]:
    """Return text between the first start_marker and the next end_marker,
    stripping ANSI codes and sky exec's `(sky-cmd, pid=N)` line prefix."""
    in_block = False
    out: List[str] = []
    for raw in text.splitlines():
        line = _ANSI_RE.sub('', raw)
        if in_block and end_marker in line:
            break
        if in_block:
            out.append(_PREFIX_RE.sub('', line))
        elif start_marker in line:
            in_block = True
    return '\n'.join(out) if out else None


def run_benchmark(names: List[str], cfg: BenchmarkConfig,
                  victims: List[str]) -> None:
    """Run the benchmark on every worker in parallel via a single sky exec
    per worker. Saves extracted result JSON to cfg.output_dir/worker_<i>/."""
    print(f'\n=== Running benchmark on {len(names)} workers ===', flush=True)
    n_workers = len(names)
    shell_timeout = max(
        (g.timeout_per_run_s * g.repeats for g in cfg.shell_generators()),
        default=0)
    outer_timeout = shell_timeout + cfg.duration_s + 600
    login_cmd = _build_login_cmd(cfg)
    os.makedirs(cfg.output_dir, exist_ok=True)

    def _run_one(wid: int, name: str) -> None:
        bash = _build_worker_bash(cfg, wid, n_workers, victims, login_cmd)
        yaml_path = _write_exec_task_yaml(bash)
        try:
            res = _run(f'sky exec -c {name} {yaml_path}',
                       capture=True,
                       timeout=outer_timeout,
                       check=False)
        finally:
            try:
                os.unlink(yaml_path)
            except OSError:
                pass
        combined = (res.stdout or '') + '\n' + (res.stderr or '')
        local_dir = os.path.join(cfg.output_dir, f'worker_{wid}')
        os.makedirs(local_dir, exist_ok=True)
        # Save raw sky-exec output for debugging (always, regardless of
        # success).
        log_path = os.path.join(local_dir, 'sky_exec.log')
        with open(log_path, 'w') as f:
            f.write(combined)
        # Also save the .sh we ran, for reproducibility.
        with open(os.path.join(local_dir, 'worker.sh'), 'w') as f:
            f.write(bash)
        rc = res.returncode
        content = _extract_between(combined, _RESULT_START, _RESULT_END)
        if content is None:
            print(
                f'  {name}: NO result markers (sky exec rc={rc}, '
                f'see {log_path})',
                file=sys.stderr,
                flush=True)
            return
        # Save what was extracted (the raw base64+gzip blob), before
        # decoding, so the user can inspect it on failure.
        raw_path = os.path.join(local_dir, f'results_w{wid}.raw')
        with open(raw_path, 'w') as f:
            f.write(content)
        # Worker prints the result as: gzip(json) | base64 -w 0  →  one
        # long single-line blob between the markers. Strip whitespace
        # (newlines from the surrounding `echo ""`) before decoding.
        try:
            blob = ''.join(content.split())  # drop any whitespace
            decoded = gzip.decompress(base64.b64decode(blob)).decode('utf-8')
            json.loads(decoded)  # validate
        except (binascii.Error, OSError, json.JSONDecodeError,
                UnicodeDecodeError) as e:
            head = content.strip().splitlines()[:3]
            print(
                f'  {name}: result markers found but blob did not '
                f'decode/parse ({type(e).__name__}: {e}). '
                f'First lines: {head!r}. '
                f'raw={raw_path}, log={log_path}',
                file=sys.stderr,
                flush=True)
            return
        local_path = os.path.join(local_dir, f'results_w{wid}.json')
        with open(local_path, 'w') as f:
            f.write(decoded.strip() + '\n')
        try:
            os.unlink(raw_path)
        except OSError:
            pass
        print(
            f'  {name}: result saved → {local_path} '
            f'({len(decoded)} bytes, sky exec rc={rc})',
            flush=True)

    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        futs = {pool.submit(_run_one, i, n): n for i, n in enumerate(names)}
        for fut in as_completed(futs):
            n = futs[fut]
            try:
                fut.result()
            except Exception as e:  # noqa: BLE001
                print(f'  {n} error: {e}', file=sys.stderr, flush=True)


# ── Phase 4: collect results (load previously-saved JSON from disk) ──


def collect_results(names: List[str], output_dir: str) -> List[Dict[str, Any]]:
    """Load the per-worker result JSON written by run_benchmark()."""
    print(f'\n=== Collecting results ===', flush=True)
    os.makedirs(output_dir, exist_ok=True)
    all_results: List[Dict[str, Any]] = []
    for i, name in enumerate(names):
        local_path = os.path.join(output_dir, f'worker_{i}',
                                  f'results_w{i}.json')
        if not os.path.exists(local_path):
            print(f'  {name}: {local_path} missing — worker likely failed',
                  file=sys.stderr,
                  flush=True)
            continue
        try:
            with open(local_path) as fh:
                data = json.load(fh)
        except Exception as e:  # noqa: BLE001
            print(f'  {name}: failed to parse {local_path}: {e}',
                  file=sys.stderr,
                  flush=True)
            continue
        all_results.append(data)
        shell_n = len(data.get('shell_results', []))
        gen_n = len(data.get('generator_results', {}))
        print(
            f'  {name}: loaded ({shell_n} shell records, '
            f'{gen_n} generators)',
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


def _print_ssh_bench_section(name: str, per_worker: List[Dict[str,
                                                              Any]]) -> None:
    print(f'\n── SSH bench generator: {name} ──')
    # Group records by op kind.
    ok_by_kind: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    err_by_kind: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for w in per_worker:
        for r in w.get('records', []):
            kind = r.get('kind', '?')
            if r.get('event') == 'connect' and 'duration_s' in r:
                ok_by_kind[kind].append(r)
            elif r.get('event') == 'connect_error' and 'duration_s' in r:
                err_by_kind[kind].append(r)
    # Aggregate per-op summaries across workers.
    all_kinds = set(ok_by_kind) | set(err_by_kind)
    for w in per_worker:
        all_kinds.update((w['summary'].get('per_op') or {}).keys())
    for kind in sorted(all_kinds):
        attempts = sum(((w['summary'].get('per_op') or {}).get(kind) or {}
                       ).get('attempts', 0) for w in per_worker)
        succeeded = sum(((w['summary'].get('per_op') or {}).get(kind) or {}
                        ).get('succeeded', 0) for w in per_worker)
        failed = sum(((w['summary'].get('per_op') or {}).get(kind) or {}
                     ).get('failed', 0) for w in per_worker)
        budget = sum(((w['summary'].get('per_op') or {}).get(kind) or {}
                     ).get('budget_this_worker', 0) for w in per_worker)
        first = next(
            (((w['summary'].get('per_op') or {}).get(kind) or {})
             for w in per_worker
             if (w['summary'].get('per_op') or {}).get(kind)),
            {},
        )
        total_global = first.get('total_connections_global', 0)
        conc = first.get('concurrency_per_worker', 0)
        print(f'  [{kind}] concurrency_per_worker={conc}  '
              f'total_connections (global)='
              f'{total_global if total_global else "unlimited"}  '
              f'budget_sum={budget}')
        print(f'    attempts: {attempts}  succeeded: {succeeded}  '
              f'failed: {failed}')
        ok_rows = ok_by_kind.get(kind, [])
        err_rows = err_by_kind.get(kind, [])
        if ok_rows:
            s = summarize_durations(ok_rows)
            print(f'    connect time (ok):   '
                  f'p50={s["p50"]:.3f}s p95={s["p95"]:.3f}s '
                  f'p99={s["p99"]:.3f}s max={s["max"]:.3f}s n={s["n"]}')
        if err_rows:
            s = summarize_durations(err_rows)
            print(f'    connect time (err):  '
                  f'p50={s["p50"]:.3f}s p95={s["p95"]:.3f}s '
                  f'p99={s["p99"]:.3f}s max={s["max"]:.3f}s n={s["n"]}')
            seen = set()
            for r in err_rows:
                e = (r.get('error') or '').split('\n')[0][:160]
                if e and e not in seen:
                    seen.add(e)
                    print(f'      err: {e}')
                if len(seen) >= 3:
                    break


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
        elif t == 'ssh_bench':
            _print_ssh_bench_section(name, per_worker)

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
    ssh_bench_slots = sum(
        sum(op.concurrency_per_worker
            for op in g.ops)
        for g in cfg.ssh_bench_generators()) * cfg.workers.count
    print(f'\n{"─"*72}')
    print(f'Combined pressure: {shell_threads} shell threads + '
          f'{qps_total:.1f} req/s + {long_conns} long connections + '
          f'{ssh_bench_slots} ssh-bench slots '
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
