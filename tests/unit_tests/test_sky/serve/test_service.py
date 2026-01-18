from sky import exceptions
from sky.serve import serve_utils
from sky.serve import service as serve_service
from sky.serve import service_spec


class _DummyServiceSpec:
    pool = False
    load_balancing_policy = 'round_robin'
    tls_credential = None
    target_qps_per_replica = None

    @staticmethod
    def autoscaling_policy_str():
        return 'dummy'


class _DummyTask:

    def __init__(self, service):
        self.service = service
        self.storage_mounts = {}
        self.file_mounts = {}


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
                        lambda *_args, **_kwargs: False)
    monkeypatch.setattr(serve_service, '_cleanup_task_run_script',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_service.shutil, 'rmtree',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_service.serve_state, 'remove_service',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_service.serve_state, 'delete_all_versions',
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
    monkeypatch.setattr(serve_service.serve_state,
                        'get_service_controller_port',
                        lambda *_args, **_kwargs: 20001)
    monkeypatch.setattr(serve_service.serve_state,
                        'get_service_load_balancer_port',
                        lambda *_args, **_kwargs: 20002)
    monkeypatch.setattr(serve_service.serve_state,
                        'update_service_controller_pid',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_service.controller_utils, 'can_start_new_process',
                        lambda *_args, **_kwargs: True)
    _DummyProcess.instances = []


def _write_dummy_yaml(tmp_path):
    path = tmp_path / 'service.yaml'
    path.write_text('service: dummy\n', encoding='utf-8')
    return str(path)


def test_start_recovery_uses_latest_version(monkeypatch, tmp_path):
    """Use the latest version during recovery when yaml_content exists."""
    _patch_minimal_start(monkeypatch, tmp_path)
    monkeypatch.setattr(serve_service.serve_state, 'get_service_from_name',
                        lambda *_args, **_kwargs: {'name': 'svc'})
    monkeypatch.setattr(serve_service.serve_state, 'get_service_versions',
                        lambda *_args, **_kwargs: [5])
    monkeypatch.setattr(serve_service.serve_state, 'get_yaml_content',
                        lambda *_args, **_kwargs: 'service: s')
    monkeypatch.setattr(serve_service.serve_state, 'get_spec',
                        lambda *_args, **_kwargs: _DummyServiceSpec())

    def _raise_terminate(*_args, **_kwargs):
        raise exceptions.ServeUserTerminatedError('test')

    monkeypatch.setattr(serve_service, '_handle_signal', _raise_terminate)

    serve_service._start('svc', _write_dummy_yaml(tmp_path), 4, 'entrypoint')

    controller_proc = next(
        proc for proc in _DummyProcess.instances
        if proc.target == serve_service.controller.run_controller)
    assert controller_proc.args[2] == 5


def test_start_recovery_backfills_yaml_content(monkeypatch, tmp_path):
    """Backfill yaml_content from task yaml when the DB is empty."""
    _patch_minimal_start(monkeypatch, tmp_path)
    service_name = 'svc'
    service_dir = tmp_path / service_name
    service_dir.mkdir(parents=True, exist_ok=True)
    task_yaml = service_dir / 'task_v3.yaml'
    task_yaml.write_text('service: dummy\n', encoding='utf-8')

    monkeypatch.setattr(serve_service.serve_state, 'get_service_from_name',
                        lambda *_args, **_kwargs: {'name': service_name})
    monkeypatch.setattr(serve_service.serve_state, 'get_service_versions',
                        lambda *_args, **_kwargs: [3])
    monkeypatch.setattr(serve_service.serve_state, 'get_yaml_content',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_service.serve_state, 'get_spec',
                        lambda *_args, **_kwargs: None)

    backfilled = []

    def _add_or_update_version(*_args, **_kwargs):
        backfilled.append(_args[1])

    monkeypatch.setattr(serve_service.serve_state, 'add_or_update_version',
                        _add_or_update_version)

    def _raise_terminate(*_args, **_kwargs):
        raise exceptions.ServeUserTerminatedError('test')

    monkeypatch.setattr(serve_service, '_handle_signal', _raise_terminate)

    serve_service._start(service_name, _write_dummy_yaml(tmp_path), 7,
                         'entrypoint')

    assert backfilled == [3]
    controller_proc = next(
        proc for proc in _DummyProcess.instances
        if proc.target == serve_service.controller.run_controller)
    assert controller_proc.args[2] == 3


def test_start_recovery_skips_missing_yaml(monkeypatch, tmp_path):
    """Skip versions without yaml_content or task yaml during recovery."""
    _patch_minimal_start(monkeypatch, tmp_path)
    service_name = 'svc'
    service_dir = tmp_path / service_name
    service_dir.mkdir(parents=True, exist_ok=True)
    task_yaml = service_dir / 'task_v2.yaml'
    task_yaml.write_text('service: dummy\n', encoding='utf-8')

    monkeypatch.setattr(serve_service.serve_state, 'get_service_from_name',
                        lambda *_args, **_kwargs: {'name': service_name})
    monkeypatch.setattr(serve_service.serve_state, 'get_service_versions',
                        lambda *_args, **_kwargs: [3, 2])
    monkeypatch.setattr(serve_service.serve_state, 'get_yaml_content',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_service.serve_state, 'get_spec',
                        lambda *_args, **_kwargs: None)

    backfilled = []

    def _add_or_update_version(*_args, **_kwargs):
        backfilled.append(_args[1])

    monkeypatch.setattr(serve_service.serve_state, 'add_or_update_version',
                        _add_or_update_version)

    def _raise_terminate(*_args, **_kwargs):
        raise exceptions.ServeUserTerminatedError('test')

    monkeypatch.setattr(serve_service, '_handle_signal', _raise_terminate)

    missing_yaml = str(tmp_path / 'missing.yaml')
    serve_service._start(service_name, missing_yaml, 8, 'entrypoint')

    assert backfilled == [2]
    controller_proc = next(
        proc for proc in _DummyProcess.instances
        if proc.target == serve_service.controller.run_controller)
    assert controller_proc.args[2] == 2


def test_start_recovery_prefers_latest_non_null_yaml(monkeypatch, tmp_path):
    """Choose the latest version that has non-null yaml_content."""
    _patch_minimal_start(monkeypatch, tmp_path)
    monkeypatch.setattr(serve_service.serve_state, 'get_service_from_name',
                        lambda *_args, **_kwargs: {'name': 'svc'})
    monkeypatch.setattr(serve_service.serve_state, 'get_service_versions',
                        lambda *_args, **_kwargs: [4, 3])

    def _get_yaml_content(_service_name, version):
        return None if version == 4 else 'service: s'

    monkeypatch.setattr(serve_service.serve_state, 'get_yaml_content',
                        _get_yaml_content)
    monkeypatch.setattr(serve_service.serve_state, 'get_spec',
                        lambda *_args, **_kwargs: _DummyServiceSpec())

    def _raise_terminate(*_args, **_kwargs):
        raise exceptions.ServeUserTerminatedError('test')

    monkeypatch.setattr(serve_service, '_handle_signal', _raise_terminate)

    missing_yaml = str(tmp_path / 'missing.yaml')
    serve_service._start('svc', missing_yaml, 9, 'entrypoint')

    controller_proc = next(
        proc for proc in _DummyProcess.instances
        if proc.target == serve_service.controller.run_controller)
    assert controller_proc.args[2] == 3


def test_backfill_version_yaml_content(monkeypatch, tmp_path):
    """Persist yaml_content and spec when backfilling a version."""
    service_name = 'svc'
    service_dir = tmp_path / service_name
    service_dir.mkdir(parents=True, exist_ok=True)
    task_yaml = service_dir / 'task_v2.yaml'
    task_yaml.write_text('service: dummy\n', encoding='utf-8')

    monkeypatch.setattr(serve_utils, 'generate_task_yaml_file_name',
                        lambda *_args, **_kwargs: str(task_yaml))
    monkeypatch.setattr(service_spec.SkyServiceSpec, 'from_yaml_str',
                        lambda *_args, **_kwargs: 'spec')

    captured = {}

    def _add_or_update_version(_service_name, version, spec, yaml_content):
        captured['service_name'] = _service_name
        captured['version'] = version
        captured['spec'] = spec
        captured['yaml_content'] = yaml_content

    monkeypatch.setattr(serve_utils.serve_state, 'add_or_update_version',
                        _add_or_update_version)

    serve_utils.backfill_version_yaml_content(service_name, 2)

    assert captured['service_name'] == service_name
    assert captured['version'] == 2
    assert captured['spec'] == 'spec'
    assert captured['yaml_content'] == 'service: dummy\n'
