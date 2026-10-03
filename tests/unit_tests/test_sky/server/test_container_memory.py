"""Tests for the API server container's memory and process census."""
import os

import pytest

from sky.server import container_memory
from sky.server import metrics
from sky.server.requests import requests as requests_lib

MAIN_PID = 7
CONTROLLER_PID = 368

_SPAWN_ARGV = [
    '/usr/local/bin/python', '-c',
    'from multiprocessing.spawn import spawn_main; '
    'spawn_main(tracker_fd=70, pipe_handle=78)', '--multiprocessing-fork', ''
]
_KUBECTL_EXEC_ARGV = [
    'kubectl', 'exec', '--pod-running-timeout', '30s', '-n', 'default',
    '--context', 'ctx', 'pod/job-1-head', '--', '/bin/sh', '-c',
    'kubectl port-forward x', ''
]


@pytest.mark.parametrize(
    'pid,ppid,argv,comm,state,expected',
    [
        (MAIN_PID, 1, ['/usr/local/bin/python', '-m', 'sky.server.server', ''
                      ], 'python', 'S', 'main'),
        (89, MAIN_PID, _SPAWN_ARGV, 'python', 'S', 'server'),
        (206, MAIN_PID, ['SkyPilot:executor:short:206', ''
                        ], 'python', 'S', 'worker:short'),
        (207, MAIN_PID, ['SkyPilot:executor:long:207', ''
                        ], 'python', 'R', 'worker:long'),
        (208, MAIN_PID, ['SkyPilot:executor:other:208', ''
                        ], 'python', 'S', 'other'),
        (CONTROLLER_PID, 1, [
            '/usr/local/bin/python', '-u', '-msky.jobs.controller', 'uuid', ''
        ], 'python', 'S', 'controller'),
        (369, 1, ['python', '-u', '-m', 'sky.jobs.controller', 'x.yaml', ''
                 ], 'python', 'S', 'controller'),
        (88, MAIN_PID, [
            '/usr/local/bin/python', '-c',
            'from multiprocessing.resource_tracker import main;main(69)', ''
        ], 'python', 'S', 'resource_tracker'),
        (500, CONTROLLER_PID, _SPAWN_ARGV, 'python', 'S', 'python_other'),
        (501, 510, _KUBECTL_EXEC_ARGV, 'kubectl', 'S', 'kubectl_exec'),
        (502, 89, [
            '/usr/local/bin/kubectl', '--pod-running-timeout', '1s', '-n', 'ns',
            '--context', 'ctx', 'port-forward', 'pod/x', ':22', ''
        ], 'kubectl', 'S', 'kubectl_port_forward'),
        (503, 89, ['kubectl', 'get', 'pods', '-o', 'json', ''
                  ], 'kubectl', 'S', 'kubectl_other'),
        (504, 1, [
            '/usr/local/bin/python',
            '/skypilot/sky/skylet/subprocess_daemon.py', '--parent-pid', '368',
            '--proc-pid', '510', ''
        ], 'python', 'S', 'subprocess_daemon'),
        (505, 501, [
            '/usr/local/bin/python', '/usr/local/bin/aws', '--region',
            'us-east-2', 'eks', 'get-token', '--cluster-name', 'c', ''
        ], 'aws', 'S', 'aws'),
        (506, 89, ['aws', 's3', 'ls', ''], 'aws', 'S', 'aws'),
        (510, CONTROLLER_PID, [
            '/bin/sh', '-c', 'kubectl exec pod/x -- true', ''
        ], 'sh', 'S', 'shell'),
        (511, 1, [
            '/bin/bash', '-c', 'python -u -msky.jobs.controller uuid', ''
        ], 'bash', 'S', 'shell'),
        # OpenSSH retitles a ControlMaster; the title fills argv[0].
        (520, 1, ['ssh: /tmp/sky_ssh_1/ab/cd [mux]', '', '', ''
                 ], 'ssh', 'S', 'ssh_mux'),
        (521, 510, ['ssh', '-T', '-o', 'ControlMaster=auto', 'host', ''
                   ], 'ssh', 'S', 'ssh_other'),
        (530, CONTROLLER_PID, [], 'python', 'Z', 'zombie'),
        (531, CONTROLLER_PID, _KUBECTL_EXEC_ARGV, 'kubectl', 'Z', 'zombie'),
        (540, CONTROLLER_PID, ['python3', '/tmp/x.py', ''
                              ], 'python3', 'S', 'python_other'),
        (541, CONTROLLER_PID, ['python', '-c', 'print(1)', ''
                              ], 'python', 'S', 'python_other'),
        (550, 1, [
            'tini', '--', '/bin/sh', '-c', 'python -m sky.server.server', ''
        ], 'tini', 'S', 'other'),
        (551, MAIN_PID, ['tee', '-a', '/tmp/server.log', ''
                        ], 'tee', 'S', 'other'),
        # Unreadable command line: fall back to the process name.
        (560, CONTROLLER_PID, [], 'kubectl', 'S', 'kubectl_other'),
        (561, CONTROLLER_PID, [], '', 'S', 'other'),
    ])
def test_classify(pid, ppid, argv, comm, state, expected):
    assert container_memory.classify(pid, ppid, argv, comm, state,
                                     MAIN_PID) == expected


def test_types_are_a_fixed_set():
    assert len(set(container_memory.TYPES)) == len(container_memory.TYPES)
    assert set(container_memory.WORKER_GROUPS) == {
        t.value for t in requests_lib.ScheduleType
    }


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def _process(proc_dir,
             pid,
             ppid,
             argv,
             rss_anon_kib,
             threads,
             name='python',
             state='S (sleeping)',
             rss_file_kib=100):
    status = f'Name:\t{name}\nState:\t{state}\nTgid:\t{pid}\nPPid:\t{ppid}\n'
    if not state.startswith('Z'):
        status += (f'VmRSS:\t{rss_anon_kib + rss_file_kib} kB\n'
                   f'RssAnon:\t{rss_anon_kib} kB\n'
                   f'RssFile:\t{rss_file_kib} kB\n')
    status += f'Threads:\t{threads}\n'
    _write(os.path.join(proc_dir, str(pid), 'status'), status)
    _write(os.path.join(proc_dir, str(pid), 'cmdline'), '\0'.join(argv))


@pytest.fixture
def container(tmp_path):
    cgroup_dir = str(tmp_path / 'cgroup')
    proc_dir = str(tmp_path / 'proc')
    _write(os.path.join(cgroup_dir, 'memory.current'), '3871899648\n')
    _write(
        os.path.join(cgroup_dir, 'memory.stat'),
        'anon 3699441664\nfile 109613056\nkernel 62111744\n'
        'kernel_stack 9000000\nshmem 4096\nslab_reclaimable 20000000\n'
        'slab_unreclaimable 5000000\n')
    _write(os.path.join(cgroup_dir, 'memory.max'), 'max\n')
    # 999 is listed but has already exited. Zombies are not listed.
    _write(os.path.join(cgroup_dir, 'cgroup.procs'),
           '7\n89\n90\n206\n368\n501\n510\n520\n999\n')
    _write(os.path.join(proc_dir, 'self', 'cgroup'), '0::/\n')
    _process(proc_dir, MAIN_PID, 1, ['python', '-m', 'sky.server.server'], 1000,
             54)
    _process(proc_dir, 89, MAIN_PID, _SPAWN_ARGV, 400, 42)
    _process(proc_dir, 90, MAIN_PID, _SPAWN_ARGV, 300, 24)
    _process(proc_dir, 206, MAIN_PID, ['SkyPilot:executor:short:206'], 250, 17)
    _process(proc_dir, CONTROLLER_PID, 1,
             ['python', '-u', '-msky.jobs.controller', 'u'], 280, 20)
    _process(proc_dir, 501, 510, _KUBECTL_EXEC_ARGV, 16, 25, name='kubectl')
    _process(proc_dir,
             510,
             CONTROLLER_PID, ['/bin/sh', '-c', 'kubectl exec'],
             1,
             1,
             name='sh')
    _process(proc_dir,
             520,
             1, ['ssh: /tmp/sky_ssh_1/ab/cd [mux]'],
             2,
             1,
             name='ssh')
    _process(proc_dir, 530, CONTROLLER_PID, [], 0, 1, state='Z (zombie)')
    _write(os.path.join(proc_dir, '530', 'cgroup'), '0::/\n')
    # A zombie of another cgroup that shares the pid namespace.
    _process(proc_dir, 531, 1, [], 0, 1, state='Z (zombie)')
    _write(os.path.join(proc_dir, '531', 'cgroup'), '0::/../sidecar\n')
    # A live process of another cgroup.
    _process(proc_dir, 532, 1, ['pgbouncer'], 5, 1, name='pgbouncer')
    _write(os.path.join(proc_dir, '532', 'cgroup'), '0::/../sidecar\n')
    return cgroup_dir, proc_dir


def _usage(processes, threads, anon_kib, max_anon_kib, file_kib=100):
    kib = 1024
    return container_memory.TypeUsage(
        processes=processes,
        threads=threads,
        rss_anon_bytes=anon_kib * kib,
        max_rss_anon_bytes=max_anon_kib * kib,
        rss_bytes=(anon_kib + file_kib * processes) * kib)


def test_scan_counts_processes_by_type(container):
    cgroup_dir, proc_dir = container
    snapshot = container_memory.scan(main_pid=MAIN_PID,
                                     cgroup_dir=cgroup_dir,
                                     proc_dir=proc_dir)
    assert snapshot is not None
    assert snapshot.usage_bytes == 3871899648
    assert snapshot.stat_bytes == {
        'anon': 3699441664,
        'file': 109613056,
        'kernel': 62111744,
        'shmem': 4096,
        'slab_reclaimable': 20000000,
    }
    assert snapshot.unreclaimable_bytes == (3871899648 - 109613056 + 4096 -
                                            20000000)
    assert snapshot.limit_bytes is None
    expected = {t: container_memory.TypeUsage() for t in container_memory.TYPES}
    expected.update({
        'main': _usage(1, 54, 1000, 1000),
        'server': _usage(2, 66, 700, 400),
        'worker:short': _usage(1, 17, 250, 250),
        'controller': _usage(1, 20, 280, 280),
        'kubectl_exec': _usage(1, 25, 16, 16),
        'shell': _usage(1, 1, 1, 1),
        'ssh_mux': _usage(1, 1, 2, 2),
        'zombie': container_memory.TypeUsage(processes=1, threads=1),
    })
    assert snapshot.types == expected


def test_scan_reports_every_type(container):
    cgroup_dir, proc_dir = container
    _write(os.path.join(cgroup_dir, 'cgroup.procs'), '7\n')
    snapshot = container_memory.scan(main_pid=MAIN_PID,
                                     cgroup_dir=cgroup_dir,
                                     proc_dir=proc_dir)
    assert tuple(snapshot.types) == container_memory.TYPES
    counts = {t: u.processes for t, u in snapshot.types.items()}
    # The main process plus the zombie, which is found outside cgroup.procs.
    assert sum(counts.values()) == 2
    assert counts['main'] == counts['zombie'] == 1


def test_scan_leaves_out_excluded_pids(container):
    cgroup_dir, proc_dir = container
    snapshot = container_memory.scan(main_pid=MAIN_PID,
                                     cgroup_dir=cgroup_dir,
                                     proc_dir=proc_dir,
                                     exclude_pids=(501, 530))
    assert snapshot.types['kubectl_exec'].processes == 0
    assert snapshot.types['zombie'].processes == 0


def test_scan_in_subprocess_matches_scan(container):
    cgroup_dir, proc_dir = container
    in_process = container_memory.scan(main_pid=MAIN_PID,
                                       cgroup_dir=cgroup_dir,
                                       proc_dir=proc_dir)
    in_child = container_memory.scan_in_subprocess(main_pid=MAIN_PID,
                                                   cgroup_dir=cgroup_dir,
                                                   proc_dir=proc_dir)
    assert in_child is not None
    assert in_child.duration_seconds > 0
    in_child.duration_seconds = in_process.duration_seconds
    assert in_child == in_process


def test_scan_in_subprocess_without_cgroup_v2_files(tmp_path):
    assert container_memory.scan_in_subprocess(cgroup_dir=str(tmp_path),
                                               proc_dir=str(tmp_path)) is None


def test_scan_reads_memory_limit(container):
    cgroup_dir, proc_dir = container
    _write(os.path.join(cgroup_dir, 'memory.max'), '322122547200\n')
    snapshot = container_memory.scan(main_pid=MAIN_PID,
                                     cgroup_dir=cgroup_dir,
                                     proc_dir=proc_dir)
    assert snapshot.limit_bytes == 322122547200


def test_scan_without_cgroup_v2_files(tmp_path):
    assert container_memory.scan(cgroup_dir=str(tmp_path),
                                 proc_dir=str(tmp_path)) is None


def test_read_unreclaimable_bytes(container):
    cgroup_dir, _ = container
    assert container_memory.read_unreclaimable_bytes(cgroup_dir) == (
        3871899648 - 109613056 + 4096 - 20000000)


def test_read_unreclaimable_bytes_without_cgroup_v2_files(tmp_path):
    assert container_memory.read_unreclaimable_bytes(str(tmp_path)) is None


def test_unreclaimable_bytes_ignores_page_cache_but_not_shmem():
    gib = 1024**3
    # A 7 GiB file read into page cache, 3 GiB of it on tmpfs.
    stat = {'anon': 2 * gib, 'file': 7 * gib, 'shmem': 3 * gib}
    assert container_memory.unreclaimable_bytes(9 * gib, stat) == 5 * gib
    assert container_memory.unreclaimable_bytes(9 * gib, {'anon': 1}) is None


def test_unreclaimable_bytes_ignores_reclaimable_slab():
    mib = 1024**2
    # 300,000 empty files in a 1 GiB container: 650 MiB of dentry and inode
    # cache, which the kernel frees once anonymous memory needs the room.
    stat = {
        'anon': 6 * mib,
        'file': 80 * mib,
        'shmem': 0,
        'slab_reclaimable': 650 * mib,
    }
    assert container_memory.unreclaimable_bytes(737 * mib, stat) == 7 * mib


def _samples(families):
    out = {}
    for family in families:
        for sample in family.samples:
            out[(sample.name,
                 tuple(sorted(sample.labels.items())))] = (sample.value)
    return out


def test_collector_exports_snapshot(monkeypatch):
    snapshot = container_memory.Snapshot(
        usage_bytes=1000,
        stat_bytes={
            'anon': 700,
            'file': 200
        },
        unreclaimable_bytes=800,
        limit_bytes=4000,
        types={
            'controller': container_memory.TypeUsage(processes=64,
                                                     threads=9600,
                                                     rss_anon_bytes=640,
                                                     max_rss_anon_bytes=20,
                                                     rss_bytes=900),
            'kubectl_exec': container_memory.TypeUsage(processes=300,
                                                       threads=9000,
                                                       rss_anon_bytes=50,
                                                       max_rss_anon_bytes=1,
                                                       rss_bytes=80),
        },
        duration_seconds=0.25)
    monkeypatch.setattr(container_memory, 'scan_in_subprocess',
                        lambda: snapshot)
    samples = _samples(metrics.ContainerMemoryCollector().collect())
    p = 'sky_apiserver_container_'
    ctl = (('type', 'controller'),)
    kex = (('type', 'kubectl_exec'),)
    assert samples == {
        (f'{p}memory_usage_bytes', ()): 1000,
        (f'{p}memory_unreclaimable_bytes', ()): 800,
        (f'{p}memory_stat_bytes', (('stat', 'anon'),)): 700,
        (f'{p}memory_stat_bytes', (('stat', 'file'),)): 200,
        (f'{p}memory_limit_bytes', ()): 4000,
        (f'{p}processes', ctl): 64,
        (f'{p}processes', kex): 300,
        (f'{p}threads', ctl): 9600,
        (f'{p}threads', kex): 9000,
        (f'{p}rss_anon_bytes', ctl): 640,
        (f'{p}rss_anon_bytes', kex): 50,
        (f'{p}max_rss_anon_bytes', ctl): 20,
        (f'{p}max_rss_anon_bytes', kex): 1,
        (f'{p}rss_bytes', ctl): 900,
        (f'{p}rss_bytes', kex): 80,
        (f'{p}scan_duration_seconds', ()): 0.25,
    }


def test_collector_without_limit_emits_no_limit_sample(monkeypatch):
    snapshot = container_memory.Snapshot(usage_bytes=1,
                                         stat_bytes={},
                                         unreclaimable_bytes=None,
                                         limit_bytes=None,
                                         types={},
                                         duration_seconds=0.0)
    monkeypatch.setattr(container_memory, 'scan_in_subprocess',
                        lambda: snapshot)
    names = {
        name
        for name, _ in _samples(metrics.ContainerMemoryCollector().collect())
    }
    assert 'sky_apiserver_container_memory_limit_bytes' not in names
    assert 'sky_apiserver_container_memory_unreclaimable_bytes' not in names
    assert 'sky_apiserver_container_memory_usage_bytes' in names


def test_collector_outside_a_container_emits_nothing(monkeypatch):
    monkeypatch.setattr(container_memory, 'scan_in_subprocess', lambda: None)
    assert not list(metrics.ContainerMemoryCollector().collect())
