"""Unit tests for the seam between resolving auto_mounts and injecting them.

The resolver decides which volumes a launch mounts; write_cluster_config turns
that into pod volumeMounts. What is worth pinning here is the division of labour
between them: the readiness check runs on exactly the volumes that will be
mounted, and the reasons the resolver passed the others over reach the user at
the level they deserve.
"""
import pathlib
import textwrap

import pytest

from sky import clouds
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


@pytest.fixture(name='inject')
def inject_fixture(monkeypatch, tmp_path):
    """Runs write_cluster_config over a crafted resolution.

    Returns what reached the Jinja2 template, which volumes the readiness check
    was asked about, and what was logged.
    """

    def _inject(resolution):
        monkeypatch.setattr(volume_utils, 'resolve_auto_mounts',
                            lambda region: resolution)
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
        checked = []
        monkeypatch.setattr(backend_utils,
                            '_reject_not_ready_auto_mount_volume',
                            lambda name, record: checked.append(name))
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
            keep_launch_fields_in_existing_config=False)

        return rendered[-1], checked, warnings, debugs

    return _inject


class TestAutoMountInjection:

    def test_a_mounted_volume_is_checked_and_injected(self, inject):
        variables, checked, _, _ = inject(
            volume_utils.AutoMountResolution(mounted=[_mounted('vol')],
                                             skipped=[]))

        assert checked == ['vol']
        assert [v.name for v in variables['volume_mounts']] == ['vol']
        assert [v.path for v in variables['volume_mounts']] == ['/mnt/auto']

    def test_the_readiness_check_never_sees_a_volume_that_was_passed_over(
            self, inject):
        """The ordering the check depends on. Refusing a launch over a volume it
        was never going to mount -- someone else's personal volume, or one whose
        access mode rules it out -- would be a denial of service."""
        _, checked, _, _ = inject(
            volume_utils.AutoMountResolution(mounted=[],
                                             skipped=[
                                                 volume_utils.SkippedAutoMount(
                                                     'other-users-vol',
                                                     'out of scope',
                                                     is_warning=False)
                                             ]))

        assert checked == []

    def test_a_misconfiguration_is_reported_and_a_scope_miss_is_not(
            self, inject):
        _, _, warnings, debugs = inject(
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

        assert 'not found in the volume DB' in warnings
        assert 'has scope personal' in debugs
        assert 'has scope personal' not in warnings

    def test_home_relative_mount_paths_are_expanded(self, inject):
        variables, _, _, _ = inject(
            volume_utils.AutoMountResolution(
                mounted=[_mounted('vol', mount_paths=['~/data', '~'])],
                skipped=[]))

        paths = [v.path for v in variables['volume_mounts']]
        assert all(p.startswith('/') for p in paths), paths
        assert paths[1] == paths[0].rsplit('/data', maxsplit=1)[0]

    def test_a_malformed_mount_path_is_reported_and_skipped(self, inject):
        variables, _, warnings, _ = inject(
            volume_utils.AutoMountResolution(
                mounted=[_mounted('vol', mount_paths=['relative/path'])],
                skipped=[]))

        assert not variables['volume_mounts']
        assert any('Malformed' in w for w in warnings)

    def test_nothing_to_mount_leaves_the_template_alone(self, inject):
        variables, checked, warnings, _ = inject(
            volume_utils.AutoMountResolution(mounted=[], skipped=[]))

        assert not variables['volume_mounts']
        assert checked == []
        assert not warnings
