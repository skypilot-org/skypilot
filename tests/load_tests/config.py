"""Benchmark configuration: YAML schema + dataclasses.

Parses and validates the benchmark config file used by benchmark_ctl.py and
benchmark_worker.py. Supports three generator types that can run
concurrently on each worker:

  * shell      — runs a bash workload script (e.g. workloads/basic.sh)
  * qps        — open-loop Python QPS engine (sky.status, sky.queue,
                 sky.jobs.queue) at a configurable rate
  * long_conn  — holds N concurrent long-lived sessions (ssh, sky logs)

Also synthesises an equivalent config from the legacy CLI flags so existing
invocations keep working without a YAML file.
"""
from __future__ import annotations

import dataclasses
import os
from typing import Any, Dict, List, Optional

import yaml

# ── dataclasses ──────────────────────────────────────────────────────


@dataclasses.dataclass
class Target:
    endpoint: str
    # For basic auth, embed user:pass@ in the URL. For service-account tokens
    # use this field (maps to `sky api login --token <sky_...>`).
    service_account_token: Optional[str] = None
    cloud: str = 'aws'


@dataclasses.dataclass
class Workers:
    count: int = 2
    cloud: str = 'aws'
    cpus: int = 8
    memory: int = 16
    image: Optional[str] = None
    reuse: bool = False
    cleanup: bool = True
    # Install the SkyPilot Go CLI on each worker and prepend ~/.sky/bin to
    # PATH so shell workloads / login / ssh-priming use the Go binary. The
    # Python SDK (pip install skypilot-nightly) is still installed because
    # benchmark_worker.py generators rely on `import sky`.
    use_go_client: bool = False
    go_client_version: Optional[str] = None


@dataclasses.dataclass
class VictimPool:
    enabled: bool = False
    count: int = 0
    cloud: str = 'aws'
    cpus: int = 2
    memory: int = 4
    name_prefix: str = 'bench-victim'
    provision: bool = True
    teardown: bool = True

    def names(self) -> List[str]:
        return [f'{self.name_prefix}-{i}' for i in range(self.count)]


@dataclasses.dataclass
class ShellGeneratorSpec:
    name: str
    workload: str  # path relative to load_tests/
    threads_per_worker: int = 4
    repeats: int = 1
    phases: List[str] = dataclasses.field(
        default_factory=lambda: ['cluster', 'jobs'])
    timeout_per_run_s: int = 3600
    type: str = 'shell'


# Ops that always target a victim cluster — the parser sets needs_victim=True
# for these so config authors don't have to remember to toggle it.
_VICTIM_OPS = frozenset({
    'job_status',
    'cluster_queue',  # sky queue <cluster>
    'tail_logs',  # sky logs <cluster> --no-follow
    'exec',  # sky exec <cluster> "echo hello"
})

_ALL_QPS_OPS = frozenset({'status', 'jobs_queue'}) | _VICTIM_OPS


@dataclasses.dataclass
class QpsOp:
    op: str  # see _ALL_QPS_OPS
    weight: float = 1.0
    needs_victim: bool = False


@dataclasses.dataclass
class QpsGeneratorSpec:
    name: str
    target_qps: float  # GLOBAL aggregate; split per worker
    operations: List[QpsOp]
    max_inflight_per_worker: int = 50
    type: str = 'qps'


@dataclasses.dataclass
class LongConnProto:
    kind: str  # ssh_idle | logs_follow
    connections_per_worker: int = 1


@dataclasses.dataclass
class LongConnGeneratorSpec:
    name: str
    protocols: List[LongConnProto]
    health_check_interval_s: int = 30
    reconnect_on_drop: bool = True
    type: str = 'long_conn'


@dataclasses.dataclass
class SshBenchOp:
    kind: str  # ssh | sky_logs
    concurrency_per_worker: int = 4
    # Global total across all workers, split evenly. 0 = unlimited
    # (bounded by duration_s).
    total_connections: int = 0


@dataclasses.dataclass
class SshBenchGeneratorSpec:
    name: str
    ops: List[SshBenchOp]
    # SSH-specific ConnectTimeout (only applies to kind=ssh).
    connect_timeout_s: int = 15
    # Wall-clock upper bound for one attempt (both ssh and sky_logs).
    op_timeout_s: int = 60
    type: str = 'ssh_bench'


GeneratorSpec = Any  # Union of the three above


@dataclasses.dataclass
class BenchmarkConfig:
    target: Target
    workers: Workers
    duration_s: int  # wall-clock fence for non-shell gens
    generators: List[GeneratorSpec]
    victim_pool: VictimPool
    timeout_s: int = 3600  # per-workload (shell) timeout
    output_dir: str = 'bench-results'

    def shell_generators(self) -> List[ShellGeneratorSpec]:
        return [g for g in self.generators if g.type == 'shell']

    def qps_generators(self) -> List[QpsGeneratorSpec]:
        return [g for g in self.generators if g.type == 'qps']

    def long_conn_generators(self) -> List[LongConnGeneratorSpec]:
        return [g for g in self.generators if g.type == 'long_conn']

    def ssh_bench_generators(self) -> List[SshBenchGeneratorSpec]:
        return [g for g in self.generators if g.type == 'ssh_bench']

    def needs_victims(self) -> bool:
        for g in self.qps_generators():
            if any(op.needs_victim for op in g.operations):
                return True
        if self.long_conn_generators():
            return True
        if self.ssh_bench_generators():
            return True
        return False


# ── parsing ──────────────────────────────────────────────────────────


def _parse_generator(d: Dict[str, Any]) -> GeneratorSpec:
    t = d.get('type')
    name = d.get('name') or t
    if t == 'shell':
        return ShellGeneratorSpec(
            name=name,
            workload=d['workload'],
            threads_per_worker=int(d.get('threads_per_worker', 4)),
            repeats=int(d.get('repeats', 1)),
            phases=list(d.get('phases', ['cluster', 'jobs'])),
            timeout_per_run_s=int(d.get('timeout_per_run_s', 3600)),
        )
    if t == 'qps':
        ops = [
            QpsOp(
                op=o['op'],
                weight=float(o.get('weight', 1.0)),
                # Force needs_victim=True for ops that unconditionally target
                # a victim cluster (sky queue / sky logs / sky exec / job
                # status on a cluster). User can't opt out.
                needs_victim=bool(o.get('needs_victim', False)) or
                o['op'] in _VICTIM_OPS,
            ) for o in d['operations']
        ]
        return QpsGeneratorSpec(
            name=name,
            target_qps=float(d['target_qps']),
            max_inflight_per_worker=int(d.get('max_inflight_per_worker', 50)),
            operations=ops,
        )
    if t == 'long_conn':
        protos = [
            LongConnProto(kind=p['kind'],
                          connections_per_worker=int(
                              p.get('connections_per_worker', 1)))
            for p in d['protocols']
        ]
        return LongConnGeneratorSpec(
            name=name,
            protocols=protos,
            health_check_interval_s=int(d.get('health_check_interval_s', 30)),
            reconnect_on_drop=bool(d.get('reconnect_on_drop', True)),
        )
    if t == 'ssh_bench':
        raw_ops = d.get('ops')
        # Top-level concurrency_per_worker / total_connections act as
        # defaults when an op entry omits them, and as the sole spec for
        # the legacy shorthand (no `ops` list — implies a single ssh op).
        default_conc = int(d.get('concurrency_per_worker', 4))
        default_total = int(d.get('total_connections', 0))
        ops: List[SshBenchOp] = []
        if not raw_ops:
            ops.append(
                SshBenchOp(kind='ssh',
                           concurrency_per_worker=default_conc,
                           total_connections=default_total))
        else:
            for entry in raw_ops:
                if isinstance(entry, str):
                    ops.append(
                        SshBenchOp(kind=entry,
                                   concurrency_per_worker=default_conc,
                                   total_connections=default_total))
                else:
                    ops.append(
                        SshBenchOp(
                            kind=entry['kind'],
                            concurrency_per_worker=int(
                                entry.get('concurrency_per_worker',
                                          default_conc)),
                            total_connections=int(
                                entry.get('total_connections', default_total)),
                        ))
        return SshBenchGeneratorSpec(
            name=name,
            ops=ops,
            connect_timeout_s=int(d.get('connect_timeout_s', 15)),
            op_timeout_s=int(d.get('op_timeout_s', 60)),
        )
    raise ValueError(f'Unknown generator type: {t!r}')


def load_from_yaml(path: str) -> BenchmarkConfig:
    with open(path, 'r') as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f'Invalid config file {path}: expected mapping')

    target_d = raw.get('target') or {}
    target = Target(
        endpoint=target_d['endpoint'],
        service_account_token=target_d.get('service_account_token'),
        cloud=target_d.get('cloud', 'aws'),
    )

    workers_d = raw.get('workers') or {}
    workers = Workers(
        count=int(workers_d.get('count', 2)),
        cloud=workers_d.get('cloud', 'aws'),
        cpus=int(workers_d.get('cpus', 8)),
        memory=int(workers_d.get('memory', 16)),
        image=workers_d.get('image'),
        reuse=bool(workers_d.get('reuse', False)),
        cleanup=bool(workers_d.get('cleanup', True)),
        use_go_client=bool(workers_d.get('use_go_client', False)),
        go_client_version=workers_d.get('go_client_version'),
    )

    vp_d = raw.get('victim_pool') or {}
    # Default victim cloud to target.cloud: victims are provisioned on the
    # target API server, and for the ssh_idle long-connection generator to
    # actually drive websocket traffic through the API server, the victims
    # must live on a backend whose SSH is proxied through the server
    # (Kubernetes: /kubernetes-pod-ssh-proxy; Slurm: /slurm-job-ssh-proxy).
    # AWS/GCP clusters use direct TCP SSH which bypasses the API server.
    victim_pool = VictimPool(
        enabled=bool(vp_d.get('enabled', False)),
        count=int(vp_d.get('count', 0)),
        cloud=vp_d.get('cloud', target.cloud),
        cpus=int(vp_d.get('cpus', 2)),
        memory=int(vp_d.get('memory', 4)),
        name_prefix=vp_d.get('name_prefix', 'bench-victim'),
        provision=bool(vp_d.get('provision', True)),
        teardown=bool(vp_d.get('teardown', True)),
    )

    gens = [_parse_generator(g) for g in (raw.get('generators') or [])]
    if not gens:
        raise ValueError(f'Config {path} defines no generators')

    cfg = BenchmarkConfig(
        target=target,
        workers=workers,
        duration_s=int(raw.get('duration_s', 600)),
        generators=gens,
        victim_pool=victim_pool,
        timeout_s=int(raw.get('timeout_s', 3600)),
        output_dir=raw.get('output_dir', 'bench-results'),
    )
    _validate(cfg)
    return cfg


def synth_from_legacy_args(
    *,
    target_endpoint: str,
    service_account_token: Optional[str],
    cloud: str,
    worker_cloud: str,
    worker_cpus: int,
    worker_image: Optional[str],
    workers_count: int,
    threads_per_worker: int,
    repeats: int,
    workload: str,
    phases: str,
    timeout: int,
    output_dir: str,
    reuse: bool,
    cleanup: bool,
) -> BenchmarkConfig:
    """Build an equivalent config for today's CLI flags (backward compat)."""
    shell_gen = ShellGeneratorSpec(
        name='shell',
        workload=workload,
        threads_per_worker=threads_per_worker,
        repeats=repeats,
        phases=[p.strip() for p in phases.split(',') if p.strip()],
        timeout_per_run_s=timeout,
    )
    cfg = BenchmarkConfig(
        target=Target(endpoint=target_endpoint,
                      service_account_token=service_account_token,
                      cloud=cloud),
        workers=Workers(count=workers_count,
                        cloud=worker_cloud,
                        cpus=worker_cpus,
                        image=worker_image,
                        reuse=reuse,
                        cleanup=cleanup),
        duration_s=timeout,
        generators=[shell_gen],
        victim_pool=VictimPool(enabled=False),
        timeout_s=timeout,
        output_dir=output_dir,
    )
    _validate(cfg)
    return cfg


def _validate(cfg: BenchmarkConfig) -> None:
    if cfg.workers.count < 1:
        raise ValueError('workers.count must be >= 1')
    names = [g.name for g in cfg.generators]
    if len(names) != len(set(names)):
        raise ValueError(f'duplicate generator names: {names}')
    for g in cfg.qps_generators():
        if g.target_qps <= 0:
            raise ValueError(f'qps generator {g.name}: target_qps must be > 0')
        if not g.operations:
            raise ValueError(f'qps generator {g.name}: no operations')
        for op in g.operations:
            if op.op not in _ALL_QPS_OPS:
                raise ValueError(
                    f'qps generator {g.name}: unknown op {op.op!r} '
                    f'(supported: {sorted(_ALL_QPS_OPS)})')
    for g in cfg.long_conn_generators():
        for p in g.protocols:
            if p.kind not in ('ssh_idle', 'logs_follow'):
                raise ValueError(
                    f'long_conn generator {g.name}: unknown protocol '
                    f'{p.kind!r}')
    for g in cfg.ssh_bench_generators():
        if not g.ops:
            raise ValueError(f'ssh_bench generator {g.name}: no ops')
        for op in g.ops:
            if op.kind not in ('ssh', 'sky_logs'):
                raise ValueError(f'ssh_bench generator {g.name}: '
                                 f'unknown op kind {op.kind!r}')
            if op.concurrency_per_worker < 1:
                raise ValueError(f'ssh_bench generator {g.name} op '
                                 f'{op.kind}: concurrency_per_worker '
                                 'must be >= 1')
            if op.total_connections < 0:
                raise ValueError(f'ssh_bench generator {g.name} op '
                                 f'{op.kind}: total_connections must '
                                 'be >= 0')
    if cfg.needs_victims():
        if not cfg.victim_pool.enabled:
            raise ValueError('a generator requires victim clusters but '
                             'victim_pool.enabled is false')
        if cfg.victim_pool.count < 1:
            raise ValueError('victim_pool.count must be >= 1 when enabled')


# ── (de)serialisation for worker upload ─────────────────────────────


def to_yaml(cfg: BenchmarkConfig) -> str:

    def _dc(x):
        if dataclasses.is_dataclass(x):
            return {k: _dc(v) for k, v in dataclasses.asdict(x).items()}
        if isinstance(x, list):
            return [_dc(v) for v in x]
        return x

    return yaml.safe_dump(_dc(cfg), sort_keys=False)


def load_config(path: Optional[str] = None, **legacy) -> BenchmarkConfig:
    if path:
        if not os.path.exists(path):
            raise FileNotFoundError(f'Config not found: {path}')
        return load_from_yaml(path)
    return synth_from_legacy_args(**legacy)
