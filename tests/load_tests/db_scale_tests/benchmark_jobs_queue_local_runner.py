#!/usr/bin/env python3
"""Benchmark local managed-jobs operations: legacy codegen+subprocess vs direct.

This compares the legacy consolidation-mode path:
  ManagedJobCodeGen -> LocalProcessCommandRunner -> parse output

against the direct in-process path that bypasses subprocess overhead.

Benchmarked operations:
  1. queue    – load the managed jobs table
  2. job_ids  – get_all_job_ids_by_name lookup

Usage:
  # Inject 1000 fake jobs, benchmark, then clean up:
  python benchmark_jobs_queue_local_runner.py --inject 1000

  # Benchmark whatever is already in the DB:
  python benchmark_jobs_queue_local_runner.py

  # Direct path only (skip legacy subprocess):
  python benchmark_jobs_queue_local_runner.py --inject 500 --direct-only
"""

import argparse
import json
import statistics
import time
from typing import Any, Dict, List, Optional, Tuple

import sky
from sky import backends
from sky import clouds
from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils
from sky.utils import common_utils
from sky.utils import message_utils
from sky.utils import subprocess_utils
from sky.workspaces import core as workspaces_core


def _make_local_handle() -> backends.LocalResourcesHandle:
    cluster_name = 'local-benchmark-consolidation'
    return backends.LocalResourcesHandle(
        cluster_name=cluster_name,
        cluster_name_on_cloud=cluster_name,
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=sky.Resources(cloud=clouds.Cloud(),
                                         instance_type=cluster_name),
    )


# ---------------------------------------------------------------------------
# Inject / cleanup helpers
# ---------------------------------------------------------------------------

_BENCH_JOB_PREFIX = 'bench-job-'
_BENCH_WORKSPACE = 'default'


def _inject_jobs(count: int) -> List[int]:
    """Inject fake managed jobs into the local DB. Returns injected job IDs."""
    from sqlalchemy import orm  # pylint: disable=import-outside-toplevel
    import sqlalchemy  # pylint: disable=import-outside-toplevel
    user_hash = common_utils.get_user_hash()
    job_ids = []
    statuses = [
        managed_job_state.ManagedJobStatus.PENDING,
        managed_job_state.ManagedJobStatus.STARTING,
        managed_job_state.ManagedJobStatus.RUNNING,
        managed_job_state.ManagedJobStatus.SUCCEEDED,
        managed_job_state.ManagedJobStatus.FAILED,
        managed_job_state.ManagedJobStatus.CANCELLED,
    ]
    for i in range(count):
        job_name = f'{_BENCH_JOB_PREFIX}{i}'
        job_id = managed_job_state.set_job_info_without_job_id(
            name=job_name,
            workspace=_BENCH_WORKSPACE,
            entrypoint=f'echo "benchmark task {i}"',
            pool='default',
            pool_hash=None,
            user_hash=user_hash,
        )
        status = statuses[i % len(statuses)]
        resources_str = json.dumps({
            'cloud': 'aws',
            'instance_type': 'g4dn.xlarge'
        })
        managed_job_state.set_pending(
            job_id=job_id,
            task_id=0,
            task_name=job_name,
            resources_str=resources_str,
            metadata='{}',
        )
        # Directly update status in DB for non-PENDING jobs to avoid
        # needing complex state machine transitions.
        if status != managed_job_state.ManagedJobStatus.PENDING:
            engine = managed_job_state._db_manager.get_engine()  # pylint: disable=protected-access
            with orm.Session(engine) as session:
                session.execute(
                    sqlalchemy.update(managed_job_state.spot_table).where(
                        sqlalchemy.and_(
                            managed_job_state.spot_table.c.spot_job_id ==
                            job_id,
                            managed_job_state.spot_table.c.task_id == 0,
                        )).values(status=status.value))
                session.commit()
        job_ids.append(job_id)
    return job_ids


def _cleanup_jobs(job_ids: List[int]) -> int:
    """Remove previously injected benchmark jobs."""
    from sqlalchemy import orm  # pylint: disable=import-outside-toplevel
    import sqlalchemy  # pylint: disable=import-outside-toplevel
    engine = managed_job_state._db_manager.get_engine()  # pylint: disable=protected-access
    deleted = 0
    with orm.Session(engine) as session:
        for table in [
                managed_job_state.spot_table,
                managed_job_state.job_info_table,
        ]:
            result = session.execute(
                sqlalchemy.delete(table).where(
                    table.c.spot_job_id.in_(job_ids)))
            deleted += result.rowcount
        # Also clean up events
        session.execute(
            sqlalchemy.delete(managed_job_state.job_events_table).where(
                managed_job_state.job_events_table.c.spot_job_id.in_(job_ids)))
        session.commit()
    return deleted


# ---------------------------------------------------------------------------
# Queue benchmark helpers
# ---------------------------------------------------------------------------


def _get_queue_kwargs(all_users: bool, skip_finished: bool,
                      limit: Optional[int]) -> Dict[str, Any]:
    user_hashes: Optional[List[Optional[str]]] = None
    if not all_users:
        user_hashes = [common_utils.get_user_hash(), None]
    return {
        'skip_finished': skip_finished,
        'accessible_workspaces': list(
            workspaces_core.get_accessible_workspace_names()),
        'job_ids': None,
        'workspace_match': None,
        'name_match': None,
        'pool_match': None,
        'page': None,
        'limit': limit,
        'user_hashes': user_hashes,
        'statuses': None,
        'fields': None,
        'sort_by': None,
        'sort_order': None,
    }


def _legacy_queue_via_local_runner(
    handle: backends.LocalResourcesHandle,
    queue_kwargs: Dict[str, Any],
) -> Tuple[Dict[str, Any], int]:
    backend = backends.CloudVmRayBackend()
    code = managed_job_utils.ManagedJobCodeGen.get_job_table(**queue_kwargs)
    returncode, payload, stderr = backend.run_on_head(
        handle,
        code,
        require_outputs=True,
        stream_logs=False,
        separate_stderr=True,
    )
    subprocess_utils.handle_returncode(returncode, code,
                                       'Legacy local queue benchmark failed.',
                                       stderr)
    jobs, total, _, total_no_filter, status_counts = (
        managed_job_utils.load_managed_job_queue(payload))
    return {
        'jobs': jobs,
        'total': total,
        'total_no_filter': total_no_filter,
        'status_counts': status_counts,
    }, len(payload.encode('utf-8'))


def _direct_queue(queue_kwargs: Dict[str, Any]) -> Dict[str, Any]:
    return managed_job_utils.get_managed_job_queue(**queue_kwargs)


# ---------------------------------------------------------------------------
# get_all_job_ids_by_name benchmark helpers
# ---------------------------------------------------------------------------


def _legacy_job_ids_by_name(
    handle: backends.LocalResourcesHandle,
    job_name: Optional[str],
) -> List[int]:
    backend = backends.CloudVmRayBackend()
    code = managed_job_utils.ManagedJobCodeGen.get_all_job_ids_by_name(
        job_name=job_name)
    returncode, payload, stderr = backend.run_on_head(
        handle,
        code,
        require_outputs=True,
        stream_logs=False,
        separate_stderr=True,
    )
    subprocess_utils.handle_returncode(
        returncode, code, 'Legacy get_all_job_ids_by_name benchmark failed.',
        stderr)
    return message_utils.decode_payload(payload)


def _direct_job_ids_by_name(job_name: Optional[str]) -> List[int]:
    return managed_job_state.get_all_job_ids_by_name(job_name)


# ---------------------------------------------------------------------------
# cancel benchmark helpers
# ---------------------------------------------------------------------------


def _legacy_cancel(handle: backends.LocalResourcesHandle,
                   job_ids: List[int]) -> str:
    backend = backends.CloudVmRayBackend()
    code = managed_job_utils.ManagedJobCodeGen.cancel_jobs_by_id(job_ids)
    returncode, stdout, stderr = backend.run_on_head(handle,
                                                     code,
                                                     require_outputs=True,
                                                     stream_logs=False)
    subprocess_utils.handle_returncode(returncode, code,
                                       'Legacy cancel benchmark failed.',
                                       stdout + stderr)
    return stdout


def _direct_cancel(job_ids: List[int]) -> str:
    return managed_job_utils.cancel_jobs_by_id(job_ids)


# ---------------------------------------------------------------------------
# stream_logs benchmark helpers
# ---------------------------------------------------------------------------


def _legacy_stream_logs(handle: backends.LocalResourcesHandle,
                        job_id: int) -> int:
    backend = backends.CloudVmRayBackend()
    code = managed_job_utils.ManagedJobCodeGen.stream_logs(job_name=None,
                                                           job_id=job_id,
                                                           follow=False,
                                                           controller=False)
    returncode = backend.run_on_head(handle,
                                     code,
                                     stream_logs=False,
                                     process_stream=False)
    return returncode


def _direct_stream_logs(job_id: int) -> Tuple[str, int]:
    return managed_job_utils.stream_logs(job_id=job_id,
                                         job_name=None,
                                         controller=False,
                                         follow=False)


# ---------------------------------------------------------------------------
# Generic benchmark runner
# ---------------------------------------------------------------------------


def _benchmark(fn, repeats: int, warmups: int) -> Tuple[List[float], Any]:
    result = None
    for _ in range(warmups):
        result = fn()
    timings = []
    for _ in range(repeats):
        start = time.perf_counter()
        result = fn()
        timings.append(time.perf_counter() - start)
    return timings, result


def _format_stats(name: str, timings: List[float]) -> str:
    mean = statistics.mean(timings)
    median = statistics.median(timings)
    p95 = (max(timings)
           if len(timings) < 20 else statistics.quantiles(timings, n=20)[18])
    return (f'{name:<22} mean={mean:.4f}s median={median:.4f}s '
            f'p95={p95:.4f}s min={min(timings):.4f}s max={max(timings):.4f}s')


def _run_benchmark_pair(name: str,
                        legacy_fn,
                        direct_fn,
                        repeats: int,
                        warmups: int,
                        validate_fn=None,
                        skip_legacy: bool = False) -> Tuple[float, float]:
    """Run legacy and direct benchmarks side by side, return means."""
    print(f'\n--- {name} ---')
    legacy_timings = None
    legacy_result = None
    if skip_legacy:
        print('  Legacy path skipped (--direct-only).')
    else:
        try:
            legacy_timings, legacy_result = _benchmark(legacy_fn,
                                                       repeats=repeats,
                                                       warmups=warmups)
        except Exception as e:  # pylint: disable=broad-except
            err_line = str(e).split('\n', maxsplit=1)[0][:120]
            print(f'  Legacy path failed: {type(e).__name__}: {err_line}')
            print('  Use --direct-only to skip the legacy path.')

    direct_timings, direct_result = _benchmark(direct_fn,
                                               repeats=repeats,
                                               warmups=warmups)

    if legacy_timings is None:
        print(f'  {_format_stats("direct_in_process", direct_timings)}')
        return 0.0, statistics.mean(direct_timings)

    if validate_fn is not None:
        validate_fn(legacy_result, direct_result)

    print(f'  {_format_stats("legacy_codegen_runner", legacy_timings)}')
    print(f'  {_format_stats("direct_in_process", direct_timings)}')

    legacy_mean = statistics.mean(legacy_timings)
    direct_mean = statistics.mean(direct_timings)
    speedup = legacy_mean / direct_mean if direct_mean > 0 else float('inf')
    print(f'  speedup={speedup:.2f}x')
    return legacy_mean, direct_mean


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Benchmark legacy vs direct managed-jobs operations '
        'for consolidation mode.')
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--warmups', type=int, default=1)
    parser.add_argument('--all-users', action='store_true')
    parser.add_argument('--skip-finished', action='store_true')
    parser.add_argument(
        '--limit',
        type=int,
        default=None,
        help='Optional queue limit to benchmark the paginated path.',
    )
    parser.add_argument(
        '--job-name',
        type=str,
        default=None,
        help='Job name for get_all_job_ids_by_name benchmark. '
        'None means get all job ids.',
    )
    parser.add_argument(
        '--direct-only',
        action='store_true',
        help='Only run the direct in-process path (skip legacy subprocess).',
    )
    parser.add_argument(
        '--inject',
        type=int,
        default=0,
        metavar='N',
        help='Inject N fake jobs before benchmarking (cleaned up after).',
    )
    args = parser.parse_args()

    # Inject fake jobs if requested
    injected_ids: List[int] = []
    if args.inject > 0:
        print(f'Injecting {args.inject} fake managed jobs...')
        injected_ids = _inject_jobs(args.inject)
        print(f'  Injected job IDs {min(injected_ids)}-{max(injected_ids)}')

    try:
        handle = _make_local_handle()
        queue_kwargs = _get_queue_kwargs(args.all_users, args.skip_finished,
                                         args.limit)

        print('\nManaged Jobs Benchmark: Legacy Codegen+Subprocess vs Direct')
        print('=' * 60)
        print(
            f'repeats={args.repeats}  warmups={args.warmups}  '
            f'all_users={args.all_users}  skip_finished={args.skip_finished}  '
            f'limit={args.limit}')

        # 1. Queue benchmark
        def validate_queue(legacy, direct):
            legacy_result, _ = legacy
            if legacy_result['total'] != direct['total']:
                raise RuntimeError(
                    'Queue results differ: '
                    f'{legacy_result["total"]} != {direct["total"]}')

        _run_benchmark_pair(
            'queue (get_job_table)',
            lambda: _legacy_queue_via_local_runner(handle, queue_kwargs),
            lambda: _direct_queue(queue_kwargs),
            repeats=args.repeats,
            warmups=args.warmups,
            validate_fn=validate_queue,
            skip_legacy=args.direct_only,
        )

        # 2. get_all_job_ids_by_name benchmark
        def validate_job_ids(legacy, direct):
            if sorted(legacy) != sorted(direct):
                raise RuntimeError(
                    f'Job IDs results differ: {legacy} != {direct}')

        _run_benchmark_pair(
            f'get_all_job_ids_by_name(name={args.job_name!r})',
            lambda: _legacy_job_ids_by_name(handle, args.job_name),
            lambda: _direct_job_ids_by_name(args.job_name),
            repeats=args.repeats,
            warmups=args.warmups,
            validate_fn=validate_job_ids,
            skip_legacy=args.direct_only,
        )

        # 3. cancel benchmark (uses terminal-state jobs so no side effects)
        if injected_ids:
            # Pick a few terminal-state job IDs (SUCCEEDED/FAILED/CANCELLED)
            # These are every 4th, 5th, 6th job (indices 3,4,5 mod 6)
            terminal_ids = [
                jid for i, jid in enumerate(injected_ids) if i % 6 >= 3
            ][:10]
            if terminal_ids:
                import logging  # pylint: disable=import-outside-toplevel

                # Suppress "already terminal" log spam during benchmark
                jobs_logger = logging.getLogger('sky.jobs.utils')
                prev_level = jobs_logger.level
                jobs_logger.setLevel(logging.WARNING)
                try:
                    _run_benchmark_pair(
                        f'cancel_jobs_by_id({len(terminal_ids)} terminal jobs)',
                        lambda: _legacy_cancel(handle, terminal_ids),
                        lambda: _direct_cancel(terminal_ids),
                        repeats=args.repeats,
                        warmups=args.warmups,
                        skip_legacy=args.direct_only,
                    )
                finally:
                    jobs_logger.setLevel(prev_level)
        else:
            print('\n--- cancel_jobs_by_id ---')
            print('  Skipped (use --inject to create jobs for cancel '
                  'benchmark).')

        # 4. stream_logs benchmark (uses a SUCCEEDED job with a dummy log)
        if injected_ids:
            # Pick a SUCCEEDED job (every 4th job, index 3 mod 6)
            succeeded_ids = [
                jid for i, jid in enumerate(injected_ids) if i % 6 == 3
            ]
            if succeeded_ids:
                log_job_id = succeeded_ids[0]
                # Create a dummy log file for the job
                log_path = managed_job_utils.controller_log_file_for_job(
                    log_job_id, create_if_not_exists=True)
                import io  # pylint: disable=import-outside-toplevel
                with open(log_path, 'w', encoding='utf-8') as f:
                    for line_i in range(100):
                        f.write(f'[benchmark] log line {line_i}\n')

                # Suppress stdout from stream_logs during benchmark
                devnull = io.StringIO()

                def _legacy_stream_quiet():
                    import contextlib  # pylint: disable=import-outside-toplevel
                    with contextlib.redirect_stdout(devnull):
                        return _legacy_stream_logs(handle, log_job_id)

                def _direct_stream_quiet():
                    import contextlib  # pylint: disable=import-outside-toplevel
                    with contextlib.redirect_stdout(devnull):
                        return _direct_stream_logs(log_job_id)

                _run_benchmark_pair(
                    f'stream_logs(job_id={log_job_id}, follow=False)',
                    _legacy_stream_quiet,
                    _direct_stream_quiet,
                    repeats=args.repeats,
                    warmups=args.warmups,
                    skip_legacy=args.direct_only,
                )

                # Clean up the dummy log file
                import os as _os  # pylint: disable=import-outside-toplevel
                if _os.path.exists(log_path):
                    _os.remove(log_path)
        else:
            print('\n--- stream_logs ---')
            print('  Skipped (use --inject to create jobs for stream_logs '
                  'benchmark).')

        print('\n' + '=' * 60)
        print('Done. Direct paths bypass codegen generation, subprocess '
              'spawning,\nand stdout parsing — results are returned as Python '
              'objects in-process.')
    finally:
        if injected_ids:
            deleted = _cleanup_jobs(injected_ids)
            print(f'\nCleaned up {deleted} rows for '
                  f'{len(injected_ids)} injected jobs.')


if __name__ == '__main__':
    main()
