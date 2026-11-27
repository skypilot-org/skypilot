"""Tests that a cluster's instance links filter on its name on the cloud.

A cloud provisioner tags the VMs with the cluster's name on the cloud
(`handle.cluster_name_on_cloud`), which is truncated and hashed when the
display name is long. A console link that filters on the display name then
matches no VM (#11515).
"""
import urllib.parse

import pytest

from sky import global_user_state
from sky.provision import common as provision_common
from sky.skylet import constants
from sky.utils.db import db_utils

_DISPLAY_NAME = 'alice-train-myrun-20261126-b1-16'
_NAME_ON_CLOUD = 'alice-train-myrun-20261-3s-e0d13ad7'


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, str(tmp_path))
    monkeypatch.setattr(
        global_user_state,
        '_db_manager',
        db_utils.DatabaseManager(
            'state',
            global_user_state.create_table,
            post_init_fn=lambda _: global_user_state._sqlite_supports_returning(
            ),
        ),
    )


class _HandleWithClusterInfo:
    """Just enough of a cloud handle for add_or_update_cluster."""
    launched_resources = None

    def __init__(self, provider_name, provider_config):
        self.cluster_name = _DISPLAY_NAME
        self.cluster_name_on_cloud = _NAME_ON_CLOUD
        self.cached_cluster_info = provision_common.ClusterInfo(
            instances={},
            head_instance_id=None,
            provider_name=provider_name,
            provider_config=provider_config)


@pytest.mark.parametrize('provider_name,provider_config,label', [
    ('gcp', {
        'project_id': 'my-proj'
    }, 'GCP Instances'),
    ('aws', {
        'region': 'us-east-1'
    }, 'AWS Instances'),
])
def test_instance_links_filter_on_cluster_name_on_cloud(tmp_path, monkeypatch,
                                                        provider_name,
                                                        provider_config, label):
    _fresh_db(tmp_path, monkeypatch)
    global_user_state.add_or_update_cluster(
        cluster_name=_DISPLAY_NAME,
        cluster_handle=_HandleWithClusterInfo(provider_name, provider_config),
        requested_resources=set(),
        ready=True,
    )

    record = global_user_state.get_cluster_from_name(_DISPLAY_NAME)
    assert record is not None
    url = urllib.parse.unquote(urllib.parse.unquote(record['links'][label]))
    assert _NAME_ON_CLOUD in url
    assert _DISPLAY_NAME not in url
