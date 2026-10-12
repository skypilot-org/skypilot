"""Unit tests for GCP nested-virtualization passthrough.

Verifies the gcp.enable_nested_virtualization config knob flows all the way
to the rendered GCP node_config as advancedMachineFeatures.enableNestedVirtualization
(the flag Firecracker-based sandboxes need on the host VM), and that it is
absent by default.
"""
import pathlib
import tempfile
import uuid
from unittest.mock import MagicMock

import pytest

from sky import logs
from sky import resources
from sky import skypilot_config
from sky.backends import backend_utils
from sky.clouds import Region
from sky.clouds import Zone
from sky.clouds.gcp import GCP
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import schemas
from sky.utils import yaml_utils


def _setup_common_mocks(monkeypatch):
    tmp_dir = pathlib.Path(tempfile.gettempdir()) / (
        'gcp-nested-virt-test-' + uuid.uuid4().hex)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    key_file = tmp_dir / 'key'
    pub_key_file = tmp_dir / 'key.pub'
    wheel_file = tmp_dir / 'fake-wheel.whl'
    yaml_base = str(tmp_dir / 'cluster-yaml')
    key_file.write_text('fake-private-key', encoding='utf-8')
    pub_key_file.write_text('ssh-rsa AAAAfake skytest', encoding='utf-8')
    wheel_file.write_text('fake-wheel', encoding='utf-8')

    monkeypatch.setattr(common_utils, 'make_cluster_name_on_cloud',
                        lambda *args, **kwargs: args[0])
    monkeypatch.setattr(backend_utils, '_get_yaml_path_from_cluster_name',
                        lambda *args, **kwargs: yaml_base)
    monkeypatch.setattr(logs, 'get_logging_agent', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        backend_utils.auth_utils, 'get_or_generate_keys',
        lambda *args, **kwargs: (str(key_file), str(pub_key_file)))
    monkeypatch.setattr(
        GCP, 'get_project_id', lambda self, dryrun=False: 'fake-project')
    monkeypatch.setattr(
        GCP, '_get_volumes_specs',
        lambda *args, **kwargs: ([], {}))
    monkeypatch.setattr(
        backend_utils.global_user_state, 'get_cluster_yaml_str',
        lambda cluster_name: None)
    fake_compute = MagicMock()
    fake_compute.projects.return_value.get.return_value.execute.\
        return_value = {'commonInstanceMetadata': {'items': []}}
    monkeypatch.setattr('sky.adaptors.gcp.build',
                        lambda *args, **kwargs: fake_compute)
    return yaml_base, wheel_file


def _write_gcp_cluster_config(monkeypatch, overrides=None, global_config=None):
    """Renders the real GCP template with the real GCP resource vars.

    Returns the parsed node_config of the rendered cluster yaml.
    """
    yaml_base, wheel_file = _setup_common_mocks(monkeypatch)
    if global_config is None:
        global_config = {}
    config_dict = config_utils.Config.from_dict(global_config)
    monkeypatch.setattr(skypilot_config, '_get_loaded_config',
                        lambda *args, **kwargs: config_dict)
    # write_cluster_config deletes/renames the tmp yaml at the end; capture
    # the final content at commit time instead of re-reading the file.
    rendered_holder = {}
    monkeypatch.setattr(
        backend_utils.global_user_state, 'set_cluster_yaml',
        lambda cluster_name, yaml_str: rendered_holder.update(yaml=yaml_str))

    backend_utils.write_cluster_config(
        to_provision=resources.Resources(
            cloud=GCP(),
            instance_type='c3-standard-8',
            _cluster_config_overrides=overrides),
        num_nodes=1,
        cluster_config_template='gcp-ray.yml.j2',
        cluster_name='fake-gcp-nested-virt-cluster',
        local_wheel_path=wheel_file,
        wheel_hash='fake-wheel-hash',
        region=Region(name='us-west1'),
        zones=[Zone(name='us-west1-a')])
    rendered = yaml_utils.safe_load(rendered_holder['yaml'])
    return rendered['available_node_types']['ray_head_default']['node_config']


def test_gcp_nested_virtualization_off_by_default(monkeypatch):
    node_config = _write_gcp_cluster_config(monkeypatch)
    assert 'advancedMachineFeatures' not in node_config, node_config


def test_gcp_nested_virtualization_global_config(monkeypatch):
    node_config = _write_gcp_cluster_config(
        monkeypatch,
        global_config={'gcp': {'enable_nested_virtualization': True}})
    assert node_config['advancedMachineFeatures'] == {
        'enableNestedVirtualization': True
    }, node_config


def test_gcp_nested_virtualization_cluster_override(monkeypatch):
    node_config = _write_gcp_cluster_config(
        monkeypatch,
        overrides={'gcp': {'enable_nested_virtualization': True}})
    assert node_config['advancedMachineFeatures'] == {
        'enableNestedVirtualization': True
    }, node_config


def test_gcp_nested_virtualization_schema_rejects_bad_type():
    config = {
        'gcp': {
            'enable_nested_virtualization': 'yes-please'
        }
    }
    with pytest.raises(
            Exception,
            match='enable_nested_virtualization'):
        common_utils.validate_schema(config, schemas.get_config_schema())
