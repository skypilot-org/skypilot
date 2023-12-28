import tempfile
from typing import List, Optional

import pandas as pd
import pytest

from sky import clouds
from sky.utils import kubernetes_utils


def enable_all_clouds_in_monkeypatch(
    monkeypatch: pytest.MonkeyPatch,
    enabled_clouds: Optional[List[str]] = None,
) -> None:
    # Monkey-patching is required because in the test environment, no cloud is
    # enabled. The optimizer checks the environment to find enabled clouds, and
    # only generates plans within these clouds. The tests assume that all three
    # clouds are enabled, so we monkeypatch the `sky.global_user_state` module
    # to return all three clouds. We also monkeypatch `sky.check.check` so that
    # when the optimizer tries calling it to update enabled_clouds, it does not
    # raise exceptions.
    if enabled_clouds is None:
        enabled_clouds = list(clouds.CLOUD_REGISTRY.values())
    monkeypatch.setattr(
        'sky.global_user_state.get_enabled_clouds',
        lambda: enabled_clouds,
    )
    monkeypatch.setattr('sky.check.check', lambda *_args, **_kwargs: None)
    config_file_backup = tempfile.NamedTemporaryFile(
        prefix='tmp_backup_config_default', delete=False)
    monkeypatch.setattr('sky.clouds.gcp.GCP_CONFIG_SKY_BACKUP_PATH',
                        config_file_backup.name)
    monkeypatch.setattr(
        'sky.clouds.gcp.DEFAULT_GCP_APPLICATION_CREDENTIAL_PATH',
        config_file_backup.name)
    monkeypatch.setenv('OCI_CONFIG', config_file_backup.name)

    az_mappings = pd.read_csv('tests/default_aws_az_mappings.csv')

    def _get_az_mappings(_):
        return az_mappings

    monkeypatch.setattr(
        'sky.clouds.service_catalog.aws_catalog._get_az_mappings',
        _get_az_mappings)

    monkeypatch.setattr('sky.backends.backend_utils.check_owner_identity',
                        lambda _: None)

    monkeypatch.setattr(
        'sky.clouds.utils.gcp_utils.list_reservations_for_instance_type_in_zone',
        lambda *_args, **_kwargs: [])

    for cloud in enabled_clouds:
        if hasattr(cloud, 'check_quota_available'):
            attr = (f'{cloud.__module__}.{cloud.__class__.__name__}.'
                    'check_quota_available')
            monkeypatch.setattr(attr, lambda *_args, **_kwargs: True)

    # Monkey patch Kubernetes resource detection since it queries
    # the cluster to detect available cluster resources.
    monkeypatch.setattr(
        'sky.utils.kubernetes_utils.detect_gpu_label_formatter',
        lambda *_args, **_kwargs: [kubernetes_utils.SkyPilotLabelFormatter, {}])
    monkeypatch.setattr('sky.utils.kubernetes_utils.detect_gpu_resource',
                        lambda *_args, **_kwargs: [True, []])
    monkeypatch.setattr('sky.utils.kubernetes_utils.check_instance_fits',
                        lambda *_args, **_kwargs: [True, ''])
