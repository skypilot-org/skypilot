"""Tests for the default queue admission wait of replica launches."""
import contextlib
from unittest import mock

from sky import execution
from sky.utils import config_utils

_KEYS = ('kubernetes', 'kueue', 'admission_timeout')


def _install(monkeypatch, config):
    monkeypatch.setattr(execution.skypilot_config, 'to_dict',
                        lambda: config_utils.Config(config))
    replaced = []

    @contextlib.contextmanager
    def fake_replace(new_config):
        replaced.append(new_config)
        yield

    monkeypatch.setattr(execution.skypilot_config,
                        'replace_skypilot_config_in_process', fake_replace)
    return replaced


class TestReplicaQueueAdmissionWait:

    def test_regular_launch_untouched(self, monkeypatch):
        replaced = _install(monkeypatch, {})
        with execution._replica_queue_admission_wait(
                is_launched_by_sky_serve_controller=False):
            pass
        assert not replaced

    def test_replica_launch_defaults_to_indefinite_wait(self, monkeypatch):
        replaced = _install(
            monkeypatch,
            {'kubernetes': {
                'kueue': {
                    'local_queue_name': 'team-a'
                }
            }})
        with execution._replica_queue_admission_wait(
                is_launched_by_sky_serve_controller=True):
            pass
        assert len(replaced) == 1
        assert replaced[0].get_nested(_KEYS, None) == -1
        # Sibling keys are preserved.
        assert replaced[0].get_nested(
            ('kubernetes', 'kueue', 'local_queue_name'), None) == 'team-a'

    def test_replica_launch_without_kubernetes_config(self, monkeypatch):
        replaced = _install(monkeypatch, {})
        with execution._replica_queue_admission_wait(
                is_launched_by_sky_serve_controller=True):
            pass
        assert len(replaced) == 1
        assert replaced[0].get_nested(_KEYS, None) == -1

    def test_explicit_admission_timeout_wins(self, monkeypatch):
        replaced = _install(
            monkeypatch, {'kubernetes': {
                'kueue': {
                    'admission_timeout': 3600
                }
            }})
        with execution._replica_queue_admission_wait(
                is_launched_by_sky_serve_controller=True):
            pass
        assert not replaced
