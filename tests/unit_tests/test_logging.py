import os
from pathlib import Path
from unittest import mock

import pytest

from sky import sky_logging
from sky.skylet import constants


def test_add_debug_log_handler_writes_log(tmp_path: Path, monkeypatch):
    # Make HOME hermetic for any expanduser calls
    monkeypatch.setenv('HOME', str(tmp_path))
    # Enable feature
    monkeypatch.setenv(constants.ENV_VAR_ENABLE_REQUEST_DEBUG_LOGGING, 'true')
    # Redirect debug log directory to a temp path
    debug_dir = tmp_path / '.sky' / 'api_server' / 'request_debug_logs'
    monkeypatch.setattr(sky_logging, 'DEBUG_LOG_DIR', str(debug_dir))

    request_id = 'req-test-123'
    log_path = debug_dir / f'{request_id}.log'

    with sky_logging.add_debug_log_handler(request_id):
        logger = sky_logging.init_logger('sky.test')
        provision_logger = sky_logging.logging.getLogger('sky.provision')
        logger.debug('hello from test logger')
        provision_logger.debug('hello from provision logger')

    assert log_path.exists(), 'debug log file was not created'
    content = log_path.read_text(encoding='utf-8')
    assert 'hello from test logger' in content
    assert 'hello from provision logger' in content


def test_add_debug_log_handler_noop_when_disabled(tmp_path: Path, monkeypatch):
    # Make HOME hermetic for any expanduser calls
    monkeypatch.setenv('HOME', str(tmp_path))
    # Ensure disabled
    monkeypatch.delenv(constants.ENV_VAR_ENABLE_REQUEST_DEBUG_LOGGING,
                       raising=False)
    debug_dir = tmp_path / '.sky' / 'api_server' / 'request_debug_logs'
    monkeypatch.setattr(sky_logging, 'DEBUG_LOG_DIR', str(debug_dir))

    request_id = 'req-disabled-123'
    log_path = debug_dir / f'{request_id}.log'

    with sky_logging.add_debug_log_handler(request_id):
        logger = sky_logging.init_logger('sky.test')
        logger.debug('this should not be written')

    assert not log_path.exists(), (
        'debug log file should not be created when disabled')


def test_will_log_follows_the_handlers():
    parent = sky_logging.logging.getLogger('will_log_test')
    child = sky_logging.logging.getLogger('will_log_test.child')
    handler = sky_logging.logging.StreamHandler()
    handler.setLevel(sky_logging.INFO)
    parent.addHandler(handler)
    parent.setLevel(sky_logging.DEBUG)
    parent.propagate = False
    try:
        # The logger lets DEBUG through; the only handler drops it.
        assert sky_logging.logging_enabled(child, sky_logging.DEBUG)
        assert not sky_logging.will_log(child, sky_logging.DEBUG)
        assert sky_logging.will_log(child, sky_logging.INFO)
        handler.setLevel(sky_logging.DEBUG)
        assert sky_logging.will_log(child, sky_logging.DEBUG)
        # A logger that stops propagating never reaches the parent's handler.
        child.propagate = False
        assert not sky_logging.will_log(child, sky_logging.DEBUG)
        assert sky_logging.will_log(child, sky_logging.WARNING)  # lastResort
    finally:
        parent.removeHandler(handler)
        child.propagate = True


def test_will_log_on_a_stand_in_logger_answers_like_logging_enabled():
    # A MagicMock's `parent` is never None and its `propagate` is truthy:
    # walking it as a handler chain would never end.
    stand_in = mock.MagicMock()
    stand_in.getEffectiveLevel.return_value = sky_logging.INFO
    assert not sky_logging.will_log(stand_in, sky_logging.DEBUG)
    assert sky_logging.will_log(stand_in, sky_logging.INFO)


@pytest.mark.parametrize('server', [False, True], ids=['client', 'server'])
def test_config_reload_skips_the_yaml_dump_nobody_logs(tmp_path: Path,
                                                       monkeypatch, server):
    from sky import skypilot_config
    from sky.utils import config_utils

    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG, raising=False)
    monkeypatch.delenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, raising=False)
    monkeypatch.delenv(constants.ENV_VAR_DB_CONNECTION_URI, raising=False)
    monkeypatch.delenv(skypilot_config.ENV_VAR_GLOBAL_CONFIG, raising=False)
    monkeypatch.delenv(skypilot_config.ENV_VAR_PROJECT_CONFIG, raising=False)
    monkeypatch.chdir(tmp_path)  # No project `.sky.yaml` from the cwd.
    if server:
        # The API server path: what sync handlers reload on every request.
        monkeypatch.setenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, '1')
    (tmp_path / '.sky').mkdir()
    (tmp_path / '.sky' / 'config.yaml').write_text('allowed_clouds: [aws]\n')
    dumps = []
    real_dump = config_utils.dump_redacted_yaml
    monkeypatch.setattr(config_utils, 'dump_redacted_yaml',
                        lambda config: dumps.append(1) or real_dump(config))
    # A logger of our own: whatever handlers the test runner attaches to
    # `sky` (log capture) must not decide the outcome.
    handler = sky_logging.logging.StreamHandler()
    handler.setLevel(sky_logging.INFO)
    own = sky_logging.logging.getLogger(f'config_reload_dump_test.{server}')
    own.setLevel(sky_logging.DEBUG)
    own.propagate = False
    # Set, not added: the runner may have attached its capture handlers.
    monkeypatch.setattr(own, 'handlers', [handler])
    monkeypatch.setattr(skypilot_config, 'logger', own)

    # pylint: disable=protected-access
    loaded = skypilot_config._get_loaded_config()
    loaded_path = skypilot_config._get_loaded_config_path()
    try:
        skypilot_config.reload_config()
        assert dumps == []
        # Control: with a handler at DEBUG the dump still happens.
        handler.setLevel(sky_logging.DEBUG)
        skypilot_config.reload_config()
        assert dumps
    finally:
        # The temp HOME's config must not leak into later tests.
        skypilot_config._set_loaded_config(loaded)
        skypilot_config._set_loaded_config_path(loaded_path)
