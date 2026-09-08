"""Unit tests for the seam between resolving auto_mounts and injecting them.

The resolver decides which volumes a launch mounts; write_cluster_config turns
that into pod volumeMounts. What is worth pinning here is the division of labour
between them: the readiness check runs on exactly the volumes that will be
mounted, and the reasons the resolver passed the others over reach the user at
the level they deserve.
"""
import pathlib
import textwrap
from typing import Any, Dict, List, NamedTuple

import pytest

from sky import clouds
from sky import exceptions
from sky import models
from sky.backends import backend_utils
from sky.resources import Resources
from sky.utils import status_lib
from sky.utils import volume as volume_utils

_MINIMAL_CLUSTER_YAML = textwrap.dedent("""\
    cluster_name: test-cluster
    provider:
      namespace: my-namespace
    available_node_types:
      ray_head_default:
        node_config:
          metadata:
            labels: {}
          spec:
            containers:
              - name: ray-node
    """)


class _Injected(NamedTuple):
    """What one write_cluster_config() run did with the volumes it was given."""
    # The variables that reached the Jinja2 template.
    variables: Dict[str, Any]
    # Volumes the readiness check was asked about, in order.
    checked: List[str]
    warnings: List[str]
    debugs: List[str]
    # Volumes whose DB record was read.
    looked_up: List[str]


def _volume_config(name: str, volume_type: str = 'k8s-pvc'):
    config = {'namespace': 'my-namespace'}
    if volume_type == 'k8s-pvc':
        config['access_mode'] = 'ReadWriteMany'
    else:
        config['host_path'] = '/mnt/data'
    return models.VolumeConfig(
        _version=1,
        name=name,
        type=volume_type,
        cloud='kubernetes',
        region='my-context',
        zone=None,
        name_on_cloud=f'{name}-pvc',
        size='10',
        config=config,
    )


def _mounted(name: str, mount_paths=('/mnt/auto',), **kwargs):
    return volume_utils.AutoMount(volume_name=name,
                                  record={
                                      'handle': _volume_config(name, **kwargs),
                                      'status': status_lib.VolumeStatus.READY,
                                      'error_message': None,
                                  },
                                  mount_paths=list(mount_paths))


def _task_volume(name: str, is_ephemeral: bool = False):
    return volume_utils.VolumeMount(path='/mnt/vol',
                                    volume_name=name,
                                    volume_config=_volume_config(name),
                                    is_ephemeral=is_ephemeral)


def _record(ready: bool = True):
    """A volume's row in the volume DB, as the status refresh leaves it."""
    if ready:
        return {'status': status_lib.VolumeStatus.READY, 'error_message': None}
    return {
        'status': status_lib.VolumeStatus.NOT_READY,
        'error_message': 'ProvisioningFailed: the tier does not exist',
    }


_NOTHING_AUTO_MOUNTED = volume_utils.AutoMountResolution(mounted=[], skipped=[])


@pytest.fixture(name='inject')
def inject_fixture(monkeypatch, tmp_path):
    """Runs write_cluster_config over a crafted resolution.

    Returns what reached the Jinja2 template, which volumes the readiness check
    was asked about, and what was logged.
    """

    def _inject(resolution, volume_mounts=None, volume_records=None):
        monkeypatch.setattr(volume_utils, 'resolve_auto_mounts',
                            lambda region: resolution)
        looked_up = []

        def _get_volume_by_name(name):
            looked_up.append(name)
            return (volume_records or {}).get(name)

        monkeypatch.setattr(backend_utils.global_user_state,
                            'get_volume_by_name', _get_volume_by_name)
        # Everything the launch would do besides resolve and inject volumes.
        monkeypatch.setattr(Resources, 'make_deploy_variables',
                            lambda *a, **kw: {})
        yaml_path = str(tmp_path / 'cluster.yml')
        monkeypatch.setattr(backend_utils, '_get_yaml_path_from_cluster_name',
                            lambda *a, **kw: yaml_path)
        monkeypatch.setattr(backend_utils, '_deterministic_cluster_yaml_hash',
                            lambda *a, **kw: 'fake-hash')
        monkeypatch.setattr('sky.check.get_cloud_credential_file_mounts',
                            lambda *a, **kw: {})
        # Folding pod_config into the rendered YAML happens after the volumes
        # have been injected, and wants a complete cluster config.
        monkeypatch.setattr(
            'sky.provision.kubernetes.utils.'
            'combine_pod_config_fields_and_metadata',
            lambda cluster_yaml_obj, **kw: cluster_yaml_obj)
        # The Kubernetes path reads the rendered YAML back to fold pod_config
        # into it, so stand in for the template with the least that survives
        # that.
        rendered = []

        def _fill_template(template, variables, output_path):
            del template  # unused
            rendered.append(variables)
            pathlib.Path(output_path).write_text(_MINIMAL_CLUSTER_YAML,
                                                 encoding='utf-8')

        monkeypatch.setattr('sky.utils.common_utils.fill_template',
                            _fill_template)
        # Recorded and then called through, so the tests see both which
        # volumes were judged and what the real judgement does.
        real_reject = backend_utils._reject_not_ready_volume  # pylint: disable=protected-access
        checked = []

        def _reject(volume_name, record, **kwargs):
            checked.append(volume_name)
            return real_reject(volume_name, record, **kwargs)

        monkeypatch.setattr(backend_utils, '_reject_not_ready_volume', _reject)
        warnings = []
        debugs = []
        monkeypatch.setattr(backend_utils.logger, 'warning', warnings.append)
        monkeypatch.setattr(backend_utils.logger, 'debug', debugs.append)

        backend_utils.write_cluster_config(
            to_provision=Resources(
                cloud=clouds.Kubernetes(),
                instance_type='2CPU--2GB').copy(region='my-context'),
            num_nodes=1,
            cluster_config_template='kubernetes-ray.yml.j2',
            cluster_name='test-cluster',
            local_wheel_path=pathlib.Path('/tmp/fake'),
            wheel_hash='fake-wheel-hash',
            region=clouds.Region(name='my-context'),
            zones=None,
            dryrun=True,
            keep_launch_fields_in_existing_config=False,
            volume_mounts=volume_mounts)

        return _Injected(rendered[-1], checked, warnings, debugs, looked_up)

    return _inject


class TestAutoMountInjection:

    def test_a_mounted_volume_is_checked_and_injected(self, inject):
        out = inject(
            volume_utils.AutoMountResolution(mounted=[_mounted('vol')],
                                             skipped=[]))

        assert out.checked == ['vol']
        assert [v.name for v in out.variables['volume_mounts']] == ['vol']
        assert [v.path for v in out.variables['volume_mounts']] == ['/mnt/auto']

    def test_the_readiness_check_never_sees_a_volume_that_was_passed_over(
            self, inject):
        """The ordering the check depends on. Refusing a launch over a volume it
        was never going to mount -- someone else's personal volume, or one whose
        access mode rules it out -- would be a denial of service."""
        out = inject(
            volume_utils.AutoMountResolution(mounted=[],
                                             skipped=[
                                                 volume_utils.SkippedAutoMount(
                                                     'other-users-vol',
                                                     'out of scope',
                                                     is_warning=False)
                                             ]))

        assert out.checked == []

    def test_a_misconfiguration_is_reported_and_a_scope_miss_is_not(
            self, inject):
        out = inject(
            volume_utils.AutoMountResolution(
                mounted=[],
                skipped=[
                    volume_utils.SkippedAutoMount('gone',
                                                  'not found in the volume DB',
                                                  is_warning=True),
                    volume_utils.SkippedAutoMount('theirs',
                                                  'has scope personal',
                                                  is_warning=False),
                ]))

        assert 'not found in the volume DB' in out.warnings
        assert 'has scope personal' in out.debugs
        assert 'has scope personal' not in out.warnings

    def test_home_relative_mount_paths_are_expanded(self, inject):
        out = inject(
            volume_utils.AutoMountResolution(
                mounted=[_mounted('vol', mount_paths=['~/data', '~'])],
                skipped=[]))

        paths = [v.path for v in out.variables['volume_mounts']]
        assert all(p.startswith('/') for p in paths), paths
        assert paths[1] == paths[0].rsplit('/data', maxsplit=1)[0]

    def test_a_malformed_mount_path_is_reported_and_skipped(self, inject):
        out = inject(
            volume_utils.AutoMountResolution(
                mounted=[_mounted('vol', mount_paths=['relative/path'])],
                skipped=[]))

        assert not out.variables['volume_mounts']
        assert any('Malformed' in w for w in out.warnings)

    def test_nothing_to_mount_leaves_the_template_alone(self, inject):
        out = inject(volume_utils.AutoMountResolution(mounted=[], skipped=[]))

        assert not out.variables['volume_mounts']
        assert out.checked == []
        assert not out.warnings


class TestTaskVolumeReadiness:
    """A volume named on the task is judged on every launch, not once.

    It is resolved when the task is submitted, which for a managed job can be
    many relaunches before the launch that mounts it.
    """

    def test_a_volume_that_became_unusable_refuses_the_launch(self, inject):
        with pytest.raises(exceptions.VolumeNotReadyError,
                           match='the tier does not exist'):
            inject(_NOTHING_AUTO_MOUNTED,
                   volume_mounts=[_task_volume('vol')],
                   volume_records={'vol': _record(ready=False)})

    def test_a_ready_volume_is_mounted(self, inject):
        out = inject(_NOTHING_AUTO_MOUNTED,
                     volume_mounts=[_task_volume('vol')],
                     volume_records={'vol': _record()})

        assert out.checked == ['vol']
        assert [v.name for v in out.variables['volume_mounts']] == ['vol']

    def test_a_record_that_cannot_be_read_mounts_anyway(self, inject):
        """A jobs controller running its own API server against its own state
        DB cannot see the volume table. That is not evidence the volume is
        unusable, and mounting does not need the record -- the config travels
        in the task."""
        out = inject(_NOTHING_AUTO_MOUNTED,
                     volume_mounts=[_task_volume('vol')],
                     volume_records={})

        assert out.looked_up == ['vol']
        assert out.checked == []
        assert [v.name for v in out.variables['volume_mounts']] == ['vol']

    def test_an_ephemeral_volume_is_not_judged(self, inject):
        """It is created by this launch, so it has no history to judge."""
        out = inject(_NOTHING_AUTO_MOUNTED,
                     volume_mounts=[_task_volume('vol', is_ephemeral=True)],
                     volume_records={'vol': _record(ready=False)})

        assert out.looked_up == []
        assert out.checked == []
