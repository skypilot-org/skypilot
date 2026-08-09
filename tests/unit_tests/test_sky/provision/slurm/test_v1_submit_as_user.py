"""Unit tests for slurm_user threading through the v1 managed-job path.

With ``slurm.submit_as_user: true`` the sbatch runs as the authenticated
user's Unix account, so every v1 follow-up (squeue / sacct / scancel /
log reads) must run as that user too. The template renders
``provider.slurm_user``; these tests pin that the v1 client and runner
constructions all pass it through.
"""
# pylint: disable=protected-access,missing-class-docstring
import types
from unittest import mock

from sky.provision.slurm import instance as slurm_instance
from sky.provision.slurm import log_streaming
from sky.provision.slurm import managed_job_runtime as mjr
from sky.utils import command_runner

_SSH_CONFIG = {
    'hostname': 'login.example.com',
    'port': 22,
    'user': 'transport',
    'private_key': '/tmp/key',
}

_V1_PROVIDER_CONFIG = {
    'skypilot_runtime': 'managed_job_v1',
    'ssh': _SSH_CONFIG,
    'partition': 'gpu',
    'slurm_user': 'alice',
}


class TestClientFromProviderConfig:

    def test_passes_slurm_user(self):
        client_cls = mock.MagicMock()
        with mock.patch.object(slurm_instance.slurm, 'SlurmClient',
                               client_cls):
            slurm_instance._slurm_client_from_provider_config(
                _V1_PROVIDER_CONFIG)
        assert client_cls.call_args.kwargs['slurm_user'] == 'alice'

    def test_defaults_to_none_without_config(self):
        config = dict(_V1_PROVIDER_CONFIG)
        del config['slurm_user']
        client_cls = mock.MagicMock()
        with mock.patch.object(slurm_instance.slurm, 'SlurmClient',
                               client_cls):
            slurm_instance._slurm_client_from_provider_config(config)
        assert client_cls.call_args.kwargs['slurm_user'] is None


def _make_v1_handle():
    handle = types.SimpleNamespace()
    handle.provision_runtime_metadata = types.SimpleNamespace(has_ray=False)
    handle.cluster_yaml = '/fake.yml'
    head_info = types.SimpleNamespace(tags={'job_id': '12345'})
    handle.cached_cluster_info = types.SimpleNamespace(
        head_instance_id='head-0', instances={'head-0': [head_info]})
    handle.launched_resources = types.SimpleNamespace(region=None)
    return handle


class TestTargetSlurmUser:

    def test_resolve_target_carries_slurm_user(self, monkeypatch):
        monkeypatch.setattr(mjr.global_user_state, 'get_cluster_yaml_dict',
                            lambda path: {'provider': _V1_PROVIDER_CONFIG})
        target = mjr._resolve_slurm_target(_make_v1_handle())
        assert target is not None
        assert target.slurm_user == 'alice'

    def test_resolve_target_defaults_to_none(self, monkeypatch):
        config = dict(_V1_PROVIDER_CONFIG)
        del config['slurm_user']
        monkeypatch.setattr(mjr.global_user_state, 'get_cluster_yaml_dict',
                            lambda path: {'provider': config})
        target = mjr._resolve_slurm_target(_make_v1_handle())
        assert target is not None
        assert target.slurm_user is None

    def test_client_from_target_passes_slurm_user(self):
        target = mjr._Target(job_id='12345',
                             ssh_config=_SSH_CONFIG,
                             partition='gpu',
                             region=None,
                             log_path=None,
                             slurm_user='alice')
        client_cls = mock.MagicMock()
        with mock.patch.object(mjr.slurm, 'SlurmClient', client_cls):
            mjr._slurm_client_from_target(target)
        assert client_cls.call_args.kwargs['slurm_user'] == 'alice'

    def test_login_node_runner_is_slurm_user_wrapped(self, tmp_path):
        key = tmp_path / 'key'
        key.write_text('')
        ssh_config = dict(_SSH_CONFIG, private_key=str(key))
        runner = mjr._login_node_runner(ssh_config, 'alice')
        assert isinstance(runner, command_runner.SlurmLoginNodeCommandRunner)
        assert runner.slurm_user == 'alice'


class TestLogStreamerWrapsAsSubmitUser:

    def _streamer(self, slurm_user):
        target = mjr._Target(job_id='12345',
                             ssh_config=_SSH_CONFIG,
                             partition='gpu',
                             region=None,
                             log_path='/home/alice/.sky_provision/x.out',
                             slurm_user=slurm_user)
        return log_streaming.SlurmLogStreamer(
            target=target,
            log_path=target.log_path,
            client=mock.MagicMock(),
            terminal_states=frozenset({'COMPLETED'}),
            sacct_get_state=mock.MagicMock(return_value=None),
            state_to_job_exit_code=mock.MagicMock(return_value=0),
            follow=False,
            tail=None,
            tail_offset=None,
            write_fn=mock.MagicMock(),
        )

    def _captured_remote_cmd(self, streamer):
        fake_runner = mock.MagicMock()
        fake_runner.ssh_base_command.return_value = ['ssh', 'fake']
        with mock.patch.object(log_streaming, '_login_node_runner',
                               return_value=fake_runner):
            with mock.patch.object(streamer, '_tail_once',
                                   return_value=0) as tail_once:
                streamer.run()
        return tail_once.call_args.args[1]

    def test_remote_cmd_wrapped_when_slurm_user_set(self):
        remote_cmd = self._captured_remote_cmd(self._streamer('alice'))
        # The tail must run as the submit user: su via sudo (transport
        # user is not root).
        assert 'sudo' in remote_cmd
        assert 'su --login' in remote_cmd
        assert 'alice' in remote_cmd
        assert 'tail' in remote_cmd

    def test_remote_cmd_untouched_without_slurm_user(self):
        remote_cmd = self._captured_remote_cmd(self._streamer(None))
        assert 'su --login' not in remote_cmd
        assert remote_cmd.startswith('tail')
