"""Compute minimum memory required for N concurrent services/job controllers.

This script reverse-engineers the resource calculation in
sky/utils/controller_utils.py to produce a table showing how much system
memory is needed to support a given number of concurrent services or job
controllers, for both consolidation and non-consolidation modes.

Usage:
    python scripts/compute_service_memory_table.py

Note on consolidation mode:
    In consolidation mode, the API server workers scale with available memory,
    consuming most of the extra memory. This means that increasing the pod
    memory has diminishing returns for increasing the number of services -
    the leftover memory for services stays roughly constant (~4GB for services,
    ~2-4GB for jobs). To support many concurrent services in consolidation
    mode, you would need to tune the API server worker configuration.
"""
import math

# --- Constants from sky/utils/controller_utils.py ---
SERVE_MONITORING_MEMORY_MB = 512
POOL_JOBS_RESOURCES_RATIO = 1
LAUNCHES_PER_WORKER = 8
LAUNCHES_PER_SERVICE = 4
JOB_WORKER_MEMORY_MB = 400
MAX_CONTROLLERS = 512 // LAUNCHES_PER_WORKER  # 64
MAXIMUM_CONTROLLER_RESERVED_MEMORY_MB = 2048

# --- Constants from sky/server/config.py ---
LONG_WORKER_MEM_GB = 0.4
SHORT_WORKER_MEM_GB = 0.3
CPU_MULTIPLIER_FOR_LONG_WORKERS = 2
MAX_MEM_PERCENT_FOR_BLOCKING = 0.6
MIN_LONG_WORKERS = 1
MIN_AVAIL_MEM_GB_CONSOLIDATION_MODE = 4
MIN_IDLE_SHORT_WORKERS = 1
DAEMON_COUNT = 3  # Approximate number of internal daemons

TARGETS = [1, 2, 4, 8, 12, 16, 24, 32, 64]

# Typical cloud CPU:memory ratio (general-purpose instances)
GB_PER_CPU = 4


def _compute_api_server_worker_memory_mb(
        system_memory_gb: float, reserved_memory_mb: float) -> float:
    """Replicate compute_server_config(deploy=True, reserved_memory_mb=...)."""
    effective_gb = system_memory_gb - (reserved_memory_mb / 1024)
    min_avail = MIN_AVAIL_MEM_GB_CONSOLIDATION_MODE
    cpu_count = max(1, int(system_memory_gb / GB_PER_CPU))

    # Long workers (deploy=True, no local cap)
    available_mem = max(0, effective_gb - min_avail)
    cpu_based = cpu_count * CPU_MULTIPLIER_FOR_LONG_WORKERS
    mem_based = int(available_mem * MAX_MEM_PERCENT_FOR_BLOCKING /
                    LONG_WORKER_MEM_GB)
    long_workers = max(MIN_LONG_WORKERS, min(cpu_based, mem_based))

    # Short workers
    reserved_mem = min_avail + (long_workers * LONG_WORKER_MEM_GB)
    short_avail = max(0, effective_gb - reserved_mem)
    min_short = MIN_IDLE_SHORT_WORKERS + DAEMON_COUNT
    short_workers = max(min_short, int(short_avail / SHORT_WORKER_MEM_GB))

    return ((long_workers * LONG_WORKER_MEM_GB +
             short_workers * SHORT_WORKER_MEM_GB) * 1024)


def _controller_reserved_mb(pool: bool) -> float:
    reserved = float(MAXIMUM_CONTROLLER_RESERVED_MEMORY_MB)
    if pool:
        reserved *= (1.0 + POOL_JOBS_RESOURCES_RATIO)
    return reserved


def _get_usable_memory_mb(system_gb: float, pool: bool,
                          consolidation: bool) -> float:
    """Replicate _get_total_usable_memory_mb from controller_utils.py."""
    reserved = _controller_reserved_mb(pool)
    total = system_gb * 1024 - reserved
    if not consolidation:
        return total
    api_worker_mem = _compute_api_server_worker_memory_mb(system_gb, reserved)
    return total - api_worker_mem


def _resource_per_unit(consolidation: bool, pool: bool,
                       is_jobs: bool) -> float:
    """Memory per unit (service or job controller) in MB."""
    if is_jobs:
        raw_resource = JOB_WORKER_MEMORY_MB
        launches = LAUNCHES_PER_WORKER
    else:
        raw_resource = SERVE_MONITORING_MEMORY_MB * POOL_JOBS_RESOURCES_RATIO
        launches = LAUNCHES_PER_WORKER if pool else LAUNCHES_PER_SERVICE

    worker_mem = 0.0
    if not consolidation:
        worker_mem = launches * LONG_WORKER_MEM_GB * 1024

    ratio = (1.0 + POOL_JOBS_RESOURCES_RATIO) if (pool or is_jobs) else 1.0
    return ratio * (raw_resource + worker_mem)


def _max_consolidation_units(pool: bool, is_jobs: bool) -> int:
    """Find the practical max units in consolidation mode.

    Because API server workers scale with memory, there's a ceiling on how
    many services/controllers you can run in consolidation mode.
    """
    rpu = _resource_per_unit(True, pool, is_jobs)
    best = 0
    effective_pool = True if is_jobs else pool
    for gb in range(4, 2049):
        usable = _get_usable_memory_mb(gb, pool=effective_pool,
                                       consolidation=True)
        n = max(1, int(usable / rpu))
        if n > best:
            best = n
    return best


def min_memory_gb(target: int, consolidation: bool, pool: bool,
                  is_jobs: bool) -> float:
    """Binary search for minimum system memory (GB) to support target units."""
    if is_jobs and target > MAX_CONTROLLERS:
        return float('inf')

    rpu = _resource_per_unit(consolidation, pool, is_jobs)
    needed_mb = target * rpu + 1

    effective_pool = True if is_jobs else pool

    # Check if this target is even achievable
    if consolidation:
        max_possible = _max_consolidation_units(effective_pool, is_jobs)
        if target > max_possible:
            return float('inf')

    low, high = 1.0, 2048.0
    for _ in range(200):
        mid = (low + high) / 2
        usable = _get_usable_memory_mb(mid, pool=effective_pool,
                                       consolidation=consolidation)
        if usable >= needed_mb:
            high = mid
        else:
            low = mid
    return math.ceil(high)


def print_table():
    # Pre-compute consolidation limits
    max_svc_consol = _max_consolidation_units(pool=False, is_jobs=False)
    max_job_consol = _max_consolidation_units(pool=True, is_jobs=True)

    print()
    print('=' * 80)
    print('Minimum system memory (GB) for N concurrent SERVICES')
    print('=' * 80)
    print(f'{"N":>6} | {"Non-consolidation (GB)":>24} | '
          f'{"Consolidation (GB)":>20}')
    print('-' * 80)
    for n in TARGETS:
        non_c = min_memory_gb(n, consolidation=False, pool=False,
                              is_jobs=False)
        c = min_memory_gb(n, consolidation=True, pool=False, is_jobs=False)
        c_str = str(c) if c != float('inf') else f'N/A (max ~{max_svc_consol})'
        print(f'{n:>6} | {non_c:>24} | {c_str:>20}')

    print()
    print('=' * 80)
    print('Minimum system memory (GB) for N concurrent JOB CONTROLLERS')
    print('=' * 80)
    print(f'{"N":>6} | {"Non-consolidation (GB)":>24} | '
          f'{"Consolidation (GB)":>20}')
    print('-' * 80)
    for n in TARGETS:
        if n > MAX_CONTROLLERS:
            print(f'{n:>6} | {"N/A (max 64)":>24} | {"N/A (max 64)":>20}')
        else:
            non_c = min_memory_gb(n, consolidation=False, pool=True,
                                  is_jobs=True)
            c = min_memory_gb(n, consolidation=True, pool=True, is_jobs=True)
            c_str = (str(c) if c != float('inf')
                     else f'N/A (max ~{max_job_consol})')
            print(f'{n:>6} | {non_c:>24} | {c_str:>20}')

    print()
    print('=' * 80)
    print('Notes:')
    print(f'  In consolidation mode, the API server workers scale with')
    print(f'  available memory, so the practical max is ~{max_svc_consol} '
          f'services / ~{max_job_consol} job controllers.')
    print(f'  Adding more pod memory has diminishing returns because the')
    print(f'  API server workers consume the extra memory.')
    print()
    print('Key parameters:')
    print(f'  Service monitoring memory: {SERVE_MONITORING_MEMORY_MB}MB each')
    print(f'  Job worker memory: {JOB_WORKER_MEMORY_MB}MB each')
    print(f'  Controller reserved: {MAXIMUM_CONTROLLER_RESERVED_MEMORY_MB}MB '
          f'(doubled for pool: '
          f'{int(MAXIMUM_CONTROLLER_RESERVED_MEMORY_MB * (1 + POOL_JOBS_RESOURCES_RATIO))}MB)')
    print(f'  Long worker memory: {LONG_WORKER_MEM_GB}GB each')
    print(f'  Non-consol launches/service: {LAUNCHES_PER_SERVICE}, '
          f'launches/job: {LAUNCHES_PER_WORKER}')
    print(f'  Assumed CPU:memory ratio: 1 vCPU per {GB_PER_CPU}GB '
          f'(affects consolidation estimates)')
    print(f'  Max job controllers (hard cap): {MAX_CONTROLLERS}')


if __name__ == '__main__':
    print_table()
