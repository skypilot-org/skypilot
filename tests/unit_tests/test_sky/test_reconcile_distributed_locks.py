"""The casbin policy lock and the config-file lock must be *distributed* locks.

These tests pin that both paths route through ``locks.get_lock``
with a stable, shared id.
"""

from unittest import mock

from sky import skypilot_config
from sky.users import permission


def test_policy_lock_uses_distributed_lock():
    with mock.patch.object(permission.locks, 'get_lock') as get_lock:
        with permission._policy_lock():  # pylint: disable=protected-access
            pass
    # Reverting to a filelock.FileLock would not call get_lock.
    get_lock.assert_called_once_with(
        permission.POLICY_UPDATE_LOCK_ID,
        permission.POLICY_UPDATE_LOCK_TIMEOUT_SECONDS,
        poll_interval=permission.POLICY_UPDATE_LOCK_POLL_INTERVAL_SECONDS)
    assert permission.POLICY_UPDATE_LOCK_ID == 'casbin-policy-update'


def test_get_skypilot_config_lock_uses_distributed_lock():
    with mock.patch('sky.utils.locks.get_lock') as get_lock:
        skypilot_config.get_skypilot_config_lock(42)
    get_lock.assert_called_once_with(
        skypilot_config.SKYPILOT_CONFIG_LOCK_ID,
        42,
        poll_interval=skypilot_config._CONFIG_LOCK_POLL_INTERVAL_SECONDS)  # pylint: disable=protected-access
    assert skypilot_config.SKYPILOT_CONFIG_LOCK_ID == 'skypilot-config-file'


def test_safe_reload_config_holds_the_config_lock():
    with mock.patch.object(skypilot_config,
                           'get_skypilot_config_lock') as get_lock, \
            mock.patch.object(skypilot_config, 'reload_config') as reload_config:
        skypilot_config.safe_reload_config()
    # The reload must happen inside the (distributed) config lock, with a
    # bounded wait (not the historical infinite timeout).
    get_lock.assert_called_once_with(
        skypilot_config._CONFIG_RELOAD_LOCK_TIMEOUT_SECONDS)  # pylint: disable=protected-access
    reload_config.assert_called_once()


def test_safe_reload_config_swallows_lock_timeout():
    from sky.utils import locks
    with mock.patch.object(skypilot_config,
                           'get_skypilot_config_lock') as get_lock, \
            mock.patch.object(skypilot_config, 'reload_config') as reload_config:
        get_lock.return_value.__enter__.side_effect = locks.LockTimeout('busy')
        # A read must not hang/raise on a wedged holder: swallow and keep the
        # currently loaded config.
        skypilot_config.safe_reload_config()
    reload_config.assert_not_called()
