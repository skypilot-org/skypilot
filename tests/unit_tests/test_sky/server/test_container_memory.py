"""Tests for the API server container's memory and process census."""
import os

import pytest

from sky.server import container_memory
from sky.server import metrics

MAIN_PID = 7

_SPAWN_ARGV = [
    '/usr/local/bin/python', '-c',
    'from multiprocessing.spawn import spawn_main; '
    'spawn_main(tracker_fd=70, pipe_handle=78)', '--multiprocessing-fork', ''
]


@pytest.mark.parametrize('pid,ppid,argv,expected', [
    (MAIN_PID, 1, ['/usr/local/bin/python', '-m', 'sky.server.server'
                  ], container_memory.TYPE_MAIN),
    (89, MAIN_PID, _SPAWN_ARGV, container_memory.TYPE_SERVER),
    (206, MAIN_PID, ['SkyPilot:executor:short:206'], 'worker:short'),
    (207, MAIN_PID, ['SkyPilot:executor:long:207'], 'worker:long'),
    (368, 1, [
        '/usr/local/bin/python', '-u', '-msky.jobs.controller', 'uuid', ''
    ], container_memory.TYPE_CONTROLLER),
    (369, 1, ['python', '-u', '-m', 'sky.jobs.controller', 'x.yaml'
             ], container_memory.TYPE_CONTROLLER),
    (88, MAIN_PID, [
        '/usr/local/bin/python', '-c',
        'from multiprocessing.resource_tracker import main;main(69)', ''
    ], container_memory.TYPE_OTHER),
    (500, 368, _SPAWN_ARGV, container_memory.TYPE_OTHER),
    (501, 368, ['kubectl', 'exec', 'pod', '--', 'sh'
               ], container_memory.TYPE_OTHER),
    (502, 1, ['/bin/bash', '-c', 'python -u -msky.jobs.controller uuid'
             ], container_memory.TYPE_OTHER),
    (503, 368, [], container_memory.TYPE_OTHER),
])
def test_classify(pid, ppid, argv, expected):
    assert container_memory.classify(pid, ppid, argv, MAIN_PID) == expected


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def _process(proc_dir, pid, ppid, argv, rss_anon_kib, threads):
    status = (f'Name:\tpython\nState:\tS (sleeping)\nTgid:\t{pid}\n'
              f'PPid:\t{ppid}\nVmRSS:\t{rss_anon_kib + 100} kB\n'
              f'RssAnon:\t{rss_anon_kib} kB\nRssFile:\t100 kB\n'
              f'Threads:\t{threads}\n')
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
        'kernel_stack 9000000\nshmem 4096\n')
    _write(os.path.join(cgroup_dir, 'memory.max'), 'max\n')
    # 999 is listed but has already exited.
    _write(os.path.join(cgroup_dir, 'cgroup.procs'),
           '7\n89\n90\n206\n368\n501\n999\n')
    _process(proc_dir, MAIN_PID, 1, ['python', '-m', 'sky.server.server'], 1000,
             54)
    _process(proc_dir, 89, MAIN_PID, _SPAWN_ARGV, 400, 42)
    _process(proc_dir, 90, MAIN_PID, _SPAWN_ARGV, 300, 24)
    _process(proc_dir, 206, MAIN_PID, ['SkyPilot:executor:short:206'], 250, 17)
    _process(proc_dir, 368, 1, ['python', '-u', '-msky.jobs.controller', 'u'],
             280, 20)
    _process(proc_dir, 501, 368, ['kubectl', 'exec'], 16, 48)
    return cgroup_dir, proc_dir


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
    }
    assert snapshot.limit_bytes is None
    kib = 1024
    assert snapshot.types == {
        'main': container_memory.TypeUsage(1, 54, 1000 * kib, 1000 * kib),
        'server': container_memory.TypeUsage(2, 66, 700 * kib, 400 * kib),
        'worker:short': container_memory.TypeUsage(1, 17, 250 * kib, 250 * kib),
        'controller': container_memory.TypeUsage(1, 20, 280 * kib, 280 * kib),
        'other': container_memory.TypeUsage(1, 48, 16 * kib, 16 * kib),
    }


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


def test_scan_of_a_zombie_counts_the_process_without_memory(container):
    cgroup_dir, proc_dir = container
    _write(os.path.join(cgroup_dir, 'cgroup.procs'), '7\n600\n')
    _write(
        os.path.join(proc_dir, '600', 'status'),
        'Name:\tkubectl\nState:\tZ (zombie)\nTgid:\t600\nPPid:\t368\n'
        'Threads:\t1\n')
    _write(os.path.join(proc_dir, '600', 'cmdline'), '')
    snapshot = container_memory.scan(main_pid=MAIN_PID,
                                     cgroup_dir=cgroup_dir,
                                     proc_dir=proc_dir)
    assert snapshot.types['other'] == container_memory.TypeUsage(1, 1, 0, 0)


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
        limit_bytes=4000,
        types={
            'controller': container_memory.TypeUsage(64, 9600, 640, 20),
            'other': container_memory.TypeUsage(2000, 96000, 300, 5),
        },
        duration_seconds=0.25)
    monkeypatch.setattr(container_memory, 'scan', lambda: snapshot)
    samples = _samples(metrics.ContainerMemoryCollector().collect())
    p = 'sky_apiserver_container_'
    ctl = (('type', 'controller'),)
    other = (('type', 'other'),)
    assert samples == {
        (f'{p}memory_usage_bytes', ()): 1000,
        (f'{p}memory_stat_bytes', (('stat', 'anon'),)): 700,
        (f'{p}memory_stat_bytes', (('stat', 'file'),)): 200,
        (f'{p}memory_limit_bytes', ()): 4000,
        (f'{p}processes', ctl): 64,
        (f'{p}processes', other): 2000,
        (f'{p}threads', ctl): 9600,
        (f'{p}threads', other): 96000,
        (f'{p}rss_anon_bytes', ctl): 640,
        (f'{p}rss_anon_bytes', other): 300,
        (f'{p}max_rss_anon_bytes', ctl): 20,
        (f'{p}max_rss_anon_bytes', other): 5,
        (f'{p}scan_duration_seconds', ()): 0.25,
    }


def test_collector_without_limit_emits_no_limit_sample(monkeypatch):
    snapshot = container_memory.Snapshot(usage_bytes=1,
                                         stat_bytes={},
                                         limit_bytes=None,
                                         types={},
                                         duration_seconds=0.0)
    monkeypatch.setattr(container_memory, 'scan', lambda: snapshot)
    names = {
        name
        for name, _ in _samples(metrics.ContainerMemoryCollector().collect())
    }
    assert 'sky_apiserver_container_memory_limit_bytes' not in names
    assert 'sky_apiserver_container_memory_usage_bytes' in names


def test_collector_outside_a_container_emits_nothing(monkeypatch):
    monkeypatch.setattr(container_memory, 'scan', lambda: None)
    assert not list(metrics.ContainerMemoryCollector().collect())
