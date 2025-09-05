import os
from pathlib import Path

from sky import sky_logging
from sky.skylet import constants


def test_add_debug_log_handler_writes_log(tmp_path: Path, monkeypatch):
    # Make HOME hermetic for any expanduser calls
    monkeypatch.setenv('HOME', str(tmp_path))
    # Enable feature
    monkeypatch.setenv(constants.ENV_VAR_ENABLE_REQUEST_DEBUG_LOGGING, 'true')
    # Redirect debug log directory to a temp path
    debug_dir = tmp_path / 'request_debug'
    monkeypatch.setattr(sky_logging, '_DEBUG_LOG_DIR', str(debug_dir))
    # Also redirect general SKY_LOGS_DIRECTORY to tmp
    monkeypatch.setattr(constants, 'SKY_LOGS_DIRECTORY',
                        str(tmp_path / 'sky_logs'))

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
    debug_dir = tmp_path / 'request_debug'
    monkeypatch.setattr(sky_logging, '_DEBUG_LOG_DIR', str(debug_dir))
    # Also redirect general SKY_LOGS_DIRECTORY to tmp
    monkeypatch.setattr(constants, 'SKY_LOGS_DIRECTORY',
                        str(tmp_path / 'sky_logs'))

    request_id = 'req-disabled-123'
    log_path = debug_dir / f'{request_id}.log'

    with sky_logging.add_debug_log_handler(request_id):
        logger = sky_logging.init_logger('sky.test')
        logger.debug('this should not be written')

    assert not log_path.exists(), (
        'debug log file should not be created when disabled')
