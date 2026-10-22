"""`lambda.remote_identity: NO_UPLOAD`: clusters get no Lambda Cloud API key.

Drives the real config layers (server file, client override, task override).
"""
from unittest import mock

import pytest

from sky import clouds
from sky import skypilot_config
from sky.backends import backend_utils
from sky.utils import yaml_utils

_REGION = 'us-west-3'
_LAMBDA = clouds.Lambda()
_NO_UPLOAD = {'lambda': {'remote_identity': 'NO_UPLOAD'}}
_LOCAL = {'lambda': {'remote_identity': 'LOCAL_CREDENTIALS'}}
_AUTO_FEATURES = (clouds.CloudImplementationFeatures.AUTOSTOP,
                  clouds.CloudImplementationFeatures.AUTODOWN)


@pytest.fixture
def server_config(tmp_path, monkeypatch):
    """Loads `config` as the server's own config for the test."""

    def load(config):
        path = tmp_path / 'config.yaml'
        path.write_text(yaml_utils.dump_yaml_str(config))
        monkeypatch.delenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG,
                           raising=False)
        monkeypatch.setattr(skypilot_config, '_GLOBAL_CONFIG_PATH', str(path))
        monkeypatch.setattr(skypilot_config, '_PROJECT_CONFIG_PATH',
                            str(tmp_path / 'nonexistent.yaml'))
        monkeypatch.setattr(skypilot_config, '_global_config_context',
                            skypilot_config.ConfigContext())
        skypilot_config.reload_config()

    yield load
    monkeypatch.undo()
    skypilot_config.reload_config()


def _resources(task_config=None):
    resources = mock.MagicMock()
    resources.cloud = _LAMBDA
    resources.region = _REGION
    resources.cluster_config_overrides = task_config or {}
    return resources


def _no_upload(task_config=None, client_config=None):
    with skypilot_config.override_skypilot_config(client_config):
        return clouds.Lambda.remote_identity_is_no_upload(
            None, _resources(task_config))


class TestRemoteIdentity:

    def test_default_uploads_the_key(self, server_config):
        server_config({})
        assert not _no_upload()

    @pytest.mark.parametrize('spelling', ['NO_UPLOAD', 'no_upload'])
    def test_server_no_upload(self, server_config, spelling):
        server_config({'lambda': {'remote_identity': spelling}})
        assert _no_upload()

    def test_a_client_may_tighten(self, server_config):
        server_config({})
        assert _no_upload(client_config=_NO_UPLOAD)

    def test_a_task_may_tighten(self, server_config):
        server_config({})
        assert _no_upload(task_config=_NO_UPLOAD)

    def test_a_client_cannot_loosen_the_server(self, server_config):
        server_config(_NO_UPLOAD)
        assert _no_upload(client_config=_LOCAL)

    def test_a_task_cannot_loosen_the_server(self, server_config):
        server_config(_NO_UPLOAD)
        assert _no_upload(task_config=_LOCAL)

    def test_another_clouds_no_upload_does_not_apply(self, server_config):
        server_config({'aws': {'remote_identity': 'NO_UPLOAD'}})
        assert not _no_upload()


class TestAutodown:

    @pytest.mark.parametrize('config,blocked', [({}, False), (_LOCAL, False),
                                                (_NO_UPLOAD, True)])
    def test_autostop_and_autodown(self, server_config, config, blocked):
        server_config(config)
        features = clouds.Lambda._unsupported_features_for_resources(  # pylint: disable=protected-access
            _resources(), region=_REGION)
        for feature in _AUTO_FEATURES:
            assert (feature in features) is blocked
        # The class-level defaults are never mutated.
        assert not set(_AUTO_FEATURES) & set(
            clouds.Lambda._CLOUD_UNSUPPORTED_FEATURES)  # pylint: disable=protected-access

    def test_the_reason_is_actionable(self, server_config):
        server_config(_NO_UPLOAD)
        features = clouds.Lambda._unsupported_features_for_resources(  # pylint: disable=protected-access
            _resources(), region=_REGION)
        reason = features[clouds.CloudImplementationFeatures.AUTODOWN]
        assert 'NO_UPLOAD' in reason and 'sky down' in reason

    @pytest.mark.parametrize('config,lacks', [({}, False), (_NO_UPLOAD, True)])
    def test_jobs_controller_leak_guard_is_skipped(self, server_config, config,
                                                   lacks):
        server_config(config)
        assert backend_utils.cluster_lacks_identity_to_autodown(
            _resources()) is lacks

    def test_other_clouds_keep_the_leak_guard(self, server_config):
        server_config(_NO_UPLOAD)
        resources = _resources()
        resources.cloud = clouds.AWS()
        assert not backend_utils.cluster_lacks_identity_to_autodown(resources)
