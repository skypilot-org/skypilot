import os

import filelock

from sky import exceptions
from sky.serve import service as serve_service


class _DummyServiceSpec:
    pool = False
    load_balancing_policy = 'round_robin'
    tls_credential = None
    target_qps_per_replica = None

    @staticmethod
    def autoscaling_policy_str():
        return 'dummy'


class _DummyTask:

    def __init__(self, service, storage_mounts=None, file_mounts=None):
        self.service = service
        self.storage_mounts = storage_mounts or {}
        self.file_mounts = file_mounts


class _DummyProcess:
    _pid_counter = 1234
    instances = []

    def __init__(self, *args, **kwargs):
        if args:
            self.target = args[0]
            self.args = args[1] if len(args) > 1 else ()
            self.kwargs = args[2] if len(args) > 2 else {}
        else:
            self.target = kwargs.get('target')
            self.args = kwargs.get('args', ())
            self.kwargs = kwargs.get('kwargs', {})
        type(self)._pid_counter += 1
        self.pid = type(self)._pid_counter
        type(self).instances.append(self)

    def start(self):
        return None

    def join(self):
        return None


class _DummyStorage:

    def __init__(self):
        self.constructed = False

    def construct(self):
        self.constructed = True


class _DummyStatus:

    def __init__(self):
        self.sky_launch_status = None
        self.sky_down_status = None


class _DummyReplicaInfo:

    def __init__(self, replica_id, cluster_name):
        self.replica_id = replica_id
        self.cluster_name = cluster_name
        self.status_property = _DummyStatus()


class _DummyThread:

    def __init__(self, cluster_name, format_exc=None):
        self.cluster_name = cluster_name
        self.format_exc = format_exc
        self._started = False

    def is_alive(self):
        return False

    def start(self):
        self._started = True
        return None

    def join(self):
        return None


def _patch_minimal_start(monkeypatch, tmp_path):

    def _dummy_from_yaml_str(*_args, **_kwargs):
        return _DummyTask(_DummyServiceSpec())

    monkeypatch.setattr(serve_service.task_lib.Task, 'from_yaml_str',
                        _dummy_from_yaml_str)
    monkeypatch.setattr(serve_service.auth_utils, 'get_or_generate_keys',
                        lambda: None)
    monkeypatch.setattr(serve_service.serve_utils,
                        'generate_remote_service_dir_name',
                        lambda name: str(tmp_path / name))
    monkeypatch.setattr(serve_service.serve_utils,
                        'generate_remote_load_balancer_log_file_name',
                        lambda name: str(tmp_path / f'{name}-lb.log'))
    monkeypatch.setattr(serve_service.backend_utils, 'get_task_resources_str',
                        lambda *_args, **_kwargs: 'resources')
    monkeypatch.setattr(serve_service.subprocess_utils,
                        'kill_children_processes', lambda *args, **kwargs: None)
    monkeypatch.setattr(serve_service.multiprocessing, 'Process', _DummyProcess)
    monkeypatch.setattr(serve_service, '_cleanup',
                        lambda *_args, **_kwargs: True)
    monkeypatch.setattr(serve_service, '_cleanup_task_run_script',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_service.serve_state, 'get_service_from_name',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_service.serve_state, 'add_or_update_version',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_service.serve_state,
                        'set_service_controller_port',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_service.serve_state,
                        'set_service_load_balancer_port',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_service.serve_state,
                        'set_service_status_and_active_versions',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_service.controller_utils, 'can_start_new_process',
                        lambda *_args, **_kwargs: True)
    _DummyProcess.instances = []


def _write_dummy_yaml(tmp_path):
    path = tmp_path / 'service.yaml'
    path.write_text('service: dummy\n', encoding='utf-8')
    return str(path)


def test_start_skips_when_controller_lock_held(monkeypatch, tmp_path):
    """Skip controller startup when another process holds the lock."""
    _patch_minimal_start(monkeypatch, tmp_path)

    class _LockWithTimeout:

        def __init__(self, path, *args, **kwargs):
            self.path = path
            del args, kwargs

        def acquire(self, timeout=None):
            if self.path.endswith('controller.lock'):
                raise filelock.Timeout(self.path)
            return True

        def release(self):
            return None

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, exc_type, exc, tb):
            self.release()
            return False

    monkeypatch.setattr(serve_service.filelock, 'FileLock', _LockWithTimeout)

    add_service_called = {'value': False}

    def _add_service(*_args, **_kwargs):
        add_service_called['value'] = True
        return True

    monkeypatch.setattr(serve_service.serve_state, 'add_service', _add_service)

    serve_service._start('svc', _write_dummy_yaml(tmp_path), 1, 'entrypoint')

    assert not add_service_called['value']


def test_start_releases_controller_lock_on_exit(monkeypatch, tmp_path):
    """Release the controller lock when the service exits."""
    _patch_minimal_start(monkeypatch, tmp_path)

    locks = []

    class _DummyLock:

        def __init__(self, path, *args, **kwargs):
            self.path = path
            self.released = False
            locks.append(self)
            del args, kwargs

        def acquire(self, timeout=None):
            return True

        def release(self):
            self.released = True
            return None

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, exc_type, exc, tb):
            self.release()
            return False

    monkeypatch.setattr(serve_service.filelock, 'FileLock', _DummyLock)

    def _raise_terminate(*_args, **_kwargs):
        raise exceptions.ServeUserTerminatedError('test')

    monkeypatch.setattr(serve_service, '_handle_signal', _raise_terminate)
    monkeypatch.setattr(serve_service.serve_state, 'add_service',
                        lambda *_args, **_kwargs: True)

    serve_service._start('svc', _write_dummy_yaml(tmp_path), 2, 'entrypoint')

    controller_lock = next(
        lock for lock in locks if lock.path.endswith('controller.lock'))
    assert controller_lock.released is True


def test_start_acquires_controller_lock_before_registering_service(
        monkeypatch, tmp_path):
    """Acquire controller lock before writing service state."""
    _patch_minimal_start(monkeypatch, tmp_path)

    lock_state = {'acquired': False}

    class _DummyLock:

        def __init__(self, path, *args, **kwargs):
            self.path = path
            del args, kwargs

        def acquire(self, timeout=None):
            if self.path.endswith('controller.lock'):
                lock_state['acquired'] = True
            return True

        def release(self):
            return None

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, exc_type, exc, tb):
            self.release()
            return False

    monkeypatch.setattr(serve_service.filelock, 'FileLock', _DummyLock)

    def _raise_terminate(*_args, **_kwargs):
        raise exceptions.ServeUserTerminatedError('test')

    monkeypatch.setattr(serve_service, '_handle_signal', _raise_terminate)

    def _add_service(*_args, **_kwargs):
        assert lock_state['acquired'] is True
        return True

    monkeypatch.setattr(serve_service.serve_state, 'add_service', _add_service)

    serve_service._start('svc', _write_dummy_yaml(tmp_path), 3, 'entrypoint')


def test_handle_signal_raises_on_terminate(monkeypatch, tmp_path):
    """Raise ServeUserTerminatedError when terminate signal is present."""
    service_name = 'svc'
    monkeypatch.setattr(serve_service.constants, 'SIGNAL_FILE_PATH',
                        str(tmp_path / 'signal_{}'))
    signal_path = serve_service.constants.SIGNAL_FILE_PATH.format(service_name)
    os.makedirs(os.path.dirname(signal_path), exist_ok=True)
    with open(signal_path, 'w', encoding='utf-8') as handle:
        handle.write('terminate')

    try:
        serve_service._handle_signal(service_name)
    except exceptions.ServeUserTerminatedError:
        pass
    else:
        raise AssertionError('Expected ServeUserTerminatedError')

    assert not os.path.exists(signal_path)


def test_handle_signal_ignores_unknown(monkeypatch, tmp_path):
    """Ignore unknown signals and delete the signal file."""
    service_name = 'svc'
    monkeypatch.setattr(serve_service.constants, 'SIGNAL_FILE_PATH',
                        str(tmp_path / 'signal_{}'))
    signal_path = serve_service.constants.SIGNAL_FILE_PATH.format(service_name)
    os.makedirs(os.path.dirname(signal_path), exist_ok=True)
    with open(signal_path, 'w', encoding='utf-8') as handle:
        handle.write('unknown')

    serve_service._handle_signal(service_name)

    assert not os.path.exists(signal_path)


def test_cleanup_storage_removes_local_mounts(monkeypatch, tmp_path):
    """Clean up local mounts and return success when no errors occur."""
    local_file = tmp_path / 'file.txt'
    local_dir = tmp_path / 'dir'
    local_file.write_text('data', encoding='utf-8')
    local_dir.mkdir()

    storage = {'s1': _DummyStorage()}
    file_mounts = {
        'file': str(local_file),
        'dir': str(local_dir),
    }

    def _dummy_from_yaml_str(*_args, **_kwargs):
        return _DummyTask(_DummyServiceSpec(),
                          storage_mounts=storage,
                          file_mounts=file_mounts)

    class _DummyBackend:

        def teardown_ephemeral_storage(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(serve_service.task_lib.Task, 'from_yaml_str',
                        _dummy_from_yaml_str)
    monkeypatch.setattr(serve_service.cloud_vm_ray_backend, 'CloudVmRayBackend',
                        _DummyBackend)
    monkeypatch.setattr(serve_service.data_utils, 'is_cloud_store_url',
                        lambda *_args, **_kwargs: False)

    assert serve_service.cleanup_storage('service: dummy') is True
    assert storage['s1'].constructed is True
    assert not local_file.exists()
    assert not local_dir.exists()


def test_cleanup_storage_returns_false_on_teardown_error(monkeypatch):
    """Return False when storage teardown raises."""

    def _dummy_from_yaml_str(*_args, **_kwargs):
        return _DummyTask(_DummyServiceSpec())

    class _DummyBackend:

        def teardown_ephemeral_storage(self, *_args, **_kwargs):
            raise RuntimeError('teardown error')

    monkeypatch.setattr(serve_service.task_lib.Task, 'from_yaml_str',
                        _dummy_from_yaml_str)
    monkeypatch.setattr(serve_service.cloud_vm_ray_backend, 'CloudVmRayBackend',
                        _DummyBackend)

    assert serve_service.cleanup_storage('service: dummy') is False


def test_cleanup_skips_missing_clusters(monkeypatch):
    """Skip replicas whose clusters no longer exist."""
    replicas = [
        _DummyReplicaInfo(1, 'missing-1'),
        _DummyReplicaInfo(2, 'missing-2'),
    ]

    monkeypatch.setattr(serve_service.serve_state, 'remove_ha_recovery_script',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_service.serve_state, 'get_replica_infos',
                        lambda *_args, **_kwargs: replicas)
    monkeypatch.setattr(serve_service.global_user_state,
                        'get_cluster_names_start_with',
                        lambda *_args, **_kwargs: [])
    removed = []
    monkeypatch.setattr(serve_service.serve_state, 'remove_replica',
                        lambda *_args, **_kwargs: removed.append(_args[1]))
    monkeypatch.setattr(serve_service.serve_state, 'get_service_versions',
                        lambda *_args, **_kwargs: [])
    monkeypatch.setattr(serve_service.serve_state, 'delete_all_versions',
                        lambda *_args, **_kwargs: None)

    assert serve_service._cleanup('svc', pool=False) is False
    assert sorted(removed) == []


def test_cleanup_terminates_replicas_success(monkeypatch):
    """Remove replicas after successful terminate threads."""
    replica = _DummyReplicaInfo(1, 'cluster-1')

    def _dummy_safe_thread(target=None, args=()):
        del target
        return _DummyThread(args[0], format_exc=None)

    monkeypatch.setattr(serve_service.serve_state, 'remove_ha_recovery_script',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_service.serve_state, 'get_replica_infos',
                        lambda *_args, **_kwargs: [replica])
    monkeypatch.setattr(serve_service.global_user_state,
                        'get_cluster_names_start_with',
                        lambda *_args, **_kwargs: ['cluster-1'])
    monkeypatch.setattr(serve_service.serve_utils,
                        'generate_replica_log_file_name',
                        lambda *_args, **_kwargs: 'log')
    monkeypatch.setattr(serve_service.thread_utils, 'SafeThread',
                        _dummy_safe_thread)
    monkeypatch.setattr(serve_service.controller_utils, 'can_terminate',
                        lambda *_args, **_kwargs: True)
    monkeypatch.setattr(serve_service.time, 'sleep',
                        lambda *_args, **_kwargs: None)
    removed = []
    monkeypatch.setattr(serve_service.serve_state, 'remove_replica',
                        lambda *_args, **_kwargs: removed.append(_args[1]))
    monkeypatch.setattr(serve_service.serve_state, 'add_or_update_replica',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_service.serve_state, 'get_service_versions',
                        lambda *_args, **_kwargs: [])
    monkeypatch.setattr(serve_service.serve_state, 'delete_all_versions',
                        lambda *_args, **_kwargs: None)

    assert serve_service._cleanup('svc', pool=False) is False
    assert removed == [1]


def test_cleanup_marks_failed_on_thread_error(monkeypatch):
    """Mark replica as failed cleanup when terminate thread errors."""
    replica = _DummyReplicaInfo(1, 'cluster-1')

    def _dummy_safe_thread(target=None, args=()):
        del target
        return _DummyThread(args[0], format_exc=RuntimeError('boom'))

    monkeypatch.setattr(serve_service.serve_state, 'remove_ha_recovery_script',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_service.serve_state, 'get_replica_infos',
                        lambda *_args, **_kwargs: [replica])
    monkeypatch.setattr(serve_service.global_user_state,
                        'get_cluster_names_start_with',
                        lambda *_args, **_kwargs: ['cluster-1'])
    monkeypatch.setattr(serve_service.serve_utils,
                        'generate_replica_log_file_name',
                        lambda *_args, **_kwargs: 'log')
    monkeypatch.setattr(serve_service.thread_utils, 'SafeThread',
                        _dummy_safe_thread)
    monkeypatch.setattr(serve_service.controller_utils, 'can_terminate',
                        lambda *_args, **_kwargs: True)
    monkeypatch.setattr(serve_service.time, 'sleep',
                        lambda *_args, **_kwargs: None)
    updates = []

    def _add_or_update_replica(*_args, **_kwargs):
        updates.append(_args[2].status_property.sky_down_status)

    monkeypatch.setattr(serve_service.serve_state, 'add_or_update_replica',
                        _add_or_update_replica)
    monkeypatch.setattr(serve_service.serve_state, 'get_service_versions',
                        lambda *_args, **_kwargs: [])
    monkeypatch.setattr(serve_service.serve_state, 'delete_all_versions',
                        lambda *_args, **_kwargs: None)

    assert serve_service._cleanup('svc', pool=False) is True
    assert (serve_service.replica_managers.common_utils.ProcessStatus.FAILED
            in updates)


def test_start_recovery_uses_latest_version(monkeypatch, tmp_path):
    """Use latest version and avoid service registration on recovery."""
    _patch_minimal_start(monkeypatch, tmp_path)
    monkeypatch.setattr(
        serve_service.serve_state, 'get_service_from_name',
        lambda *_args, **_kwargs: {'yaml_content': 'service: s'})
    monkeypatch.setattr(serve_service.serve_state, 'get_latest_version',
                        lambda *_args, **_kwargs: 5)
    monkeypatch.setattr(serve_service.serve_state,
                        'get_service_controller_port',
                        lambda *_args, **_kwargs: 20001)
    monkeypatch.setattr(serve_service.serve_state,
                        'get_service_load_balancer_port',
                        lambda *_args, **_kwargs: 20002)
    monkeypatch.setattr(serve_service.serve_state,
                        'update_service_controller_pid',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_service.serve_state, 'add_service',
                        lambda *_args, **_kwargs: False)

    def _raise_terminate(*_args, **_kwargs):
        raise exceptions.ServeUserTerminatedError('test')

    monkeypatch.setattr(serve_service, '_handle_signal', _raise_terminate)

    serve_service._start('svc', _write_dummy_yaml(tmp_path), 4, 'entrypoint')

    controller_proc = next(
        proc for proc in _DummyProcess.instances
        if proc.target == serve_service.controller.run_controller)
    controller_args = controller_proc.args
    assert controller_args[2] == 5


def test_start_pool_mode_skips_load_balancer(monkeypatch, tmp_path):
    """Skip load balancer process when running in pool mode."""
    _patch_minimal_start(monkeypatch, tmp_path)

    class _PoolServiceSpec(_DummyServiceSpec):
        pool = True

    def _dummy_from_yaml_str(*_args, **_kwargs):
        return _DummyTask(_PoolServiceSpec())

    monkeypatch.setattr(serve_service.task_lib.Task, 'from_yaml_str',
                        _dummy_from_yaml_str)

    def _raise_terminate(*_args, **_kwargs):
        raise exceptions.ServeUserTerminatedError('test')

    monkeypatch.setattr(serve_service, '_handle_signal', _raise_terminate)
    monkeypatch.setattr(serve_service.serve_state, 'add_service',
                        lambda *_args, **_kwargs: True)

    serve_service._start('svc', _write_dummy_yaml(tmp_path), 5, 'entrypoint')

    assert len(_DummyProcess.instances) == 1
    assert (_DummyProcess.instances[0].target ==
            serve_service.controller.run_controller)


def test_start_adds_initial_version(monkeypatch, tmp_path):
    """Record initial version metadata on first start."""
    _patch_minimal_start(monkeypatch, tmp_path)
    versions = []

    def _add_or_update_version(*_args, **_kwargs):
        versions.append(_args[1])

    monkeypatch.setattr(serve_service.serve_state, 'add_or_update_version',
                        _add_or_update_version)
    monkeypatch.setattr(serve_service.serve_state, 'add_service',
                        lambda *_args, **_kwargs: True)

    def _raise_terminate(*_args, **_kwargs):
        raise exceptions.ServeUserTerminatedError('test')

    monkeypatch.setattr(serve_service, '_handle_signal', _raise_terminate)

    serve_service._start('svc', _write_dummy_yaml(tmp_path), 6, 'entrypoint')

    assert versions == [serve_service.constants.INITIAL_VERSION]


def test_cleanup_task_run_script_removes_file(monkeypatch, tmp_path):
    """Remove the task run script when it exists."""
    monkeypatch.setattr(serve_service.skylet_constants,
                        'PERSISTENT_RUN_SCRIPT_DIR', str(tmp_path))
    script = tmp_path / 'sky_job_7'
    script.write_text('echo', encoding='utf-8')

    serve_service._cleanup_task_run_script(7)

    assert not script.exists()


def test_cleanup_task_run_script_missing_file(monkeypatch, tmp_path):
    """Handle missing task run script without raising."""
    monkeypatch.setattr(serve_service.skylet_constants,
                        'PERSISTENT_RUN_SCRIPT_DIR', str(tmp_path))

    serve_service._cleanup_task_run_script(8)
