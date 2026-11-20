"""-a, -u and glob selectors act on the active workspace only, and the
confirmation lists other users' targets and defaults to no."""
from typing import Optional
from unittest import mock

from click import testing as cli_testing
import pytest
import requests

from sky import exceptions
from sky.client.cli import command
from sky.server.requests.serializers import decoders
from sky.utils import status_lib


def _record(name, user, workspace: Optional[str] = 'ws-a', **extra):
    return dict(name=name,
                user_hash=f'{user}-hash',
                user_name=user,
                workspace=workspace,
                **extra)


_CLUSTERS = [
    _record('own', 'alice', status=status_lib.ClusterStatus.UP),
    _record('bobs', 'bob', status=status_lib.ClusterStatus.UP),
    _record('own-b', 'alice', 'ws-b', status=status_lib.ClusterStatus.UP),
]


@pytest.fixture(autouse=True)
def _caller(monkeypatch):
    """alice, whose active workspace is ws-a."""
    monkeypatch.setattr(
        'sky.client.sdk.get_user_workspace',
        mock.MagicMock(return_value={
            'workspace': 'ws-a',
            'source': 'explicit'
        }))
    monkeypatch.setattr(
        'sky.client.sdk.api_info',
        mock.MagicMock(return_value=mock.Mock(user=mock.Mock(id='alice-hash'))))
    monkeypatch.setattr('sky.client.cli.command._async_call_or_wait',
                        mock.MagicMock())


@pytest.fixture
def clusters(monkeypatch):
    """Lists ``_CLUSTERS`` (globs expanded) and records what is torn down."""

    def _records(clusters=None, all_users=False, **kwargs):
        del kwargs
        if clusters is None:
            return [
                dict(r)
                for r in _CLUSTERS
                if all_users or r['user_hash'] == 'alice-hash'
            ]
        return [
            dict(r) for r in _CLUSTERS if any(
                command.fnmatch.fnmatch(r['name'], c) for c in clusters)
        ]

    monkeypatch.setattr(
        'sky.client.cli.command._get_cluster_records_and_set_ssh_config',
        _records)
    down = mock.MagicMock(return_value='req')
    stop = mock.MagicMock(return_value='req')
    monkeypatch.setattr('sky.client.sdk.down', down)
    monkeypatch.setattr('sky.client.sdk.stop', stop)
    return down, stop


def _names(sdk_call):
    return sorted(call.args[0] for call in sdk_call.call_args_list)


class TestClusters:

    def test_all_users_lists_others_and_enter_aborts(self, clusters):
        down, _ = clusters
        result = cli_testing.CliRunner().invoke(command.down, ['-u'],
                                                input='\n')
        assert 'Aborted' in result.output, result.output
        assert "Terminating 2 clusters in workspace 'ws-a':" in result.output
        assert 'yours (1): own' in result.output
        assert 'other users (1): bob: bobs' in result.output
        assert '[y/N]' in result.output
        down.assert_not_called()

    def test_all_users_yes_stays_in_the_workspace(self, clusters):
        down, _ = clusters
        result = cli_testing.CliRunner().invoke(command.down, ['-u', '-y'])
        assert not result.exception, result.output
        assert _names(down) == ['bobs', 'own']

    def test_own_only_keeps_the_yes_default(self, clusters):
        _, stop = clusters
        result = cli_testing.CliRunner().invoke(command.stop, ['-a'],
                                                input='\n')
        assert not result.exception, result.output
        assert '[Y/n]' in result.output
        assert _names(stop) == ['own']

    def test_glob_stays_in_the_workspace_exact_name_does_not(self, clusters):
        down, _ = clusters
        result = cli_testing.CliRunner().invoke(command.down,
                                                ['own*', 'bobs', '-y'])
        assert not result.exception, result.output
        assert _names(down) == ['bobs', 'own']
        down.reset_mock()
        result = cli_testing.CliRunner().invoke(command.down,
                                                ['own-b', 'bob*', '-y'])
        assert not result.exception, result.output
        assert _names(down) == ['bobs', 'own-b']

    def test_exact_names_keep_the_plain_prompt(self, clusters):
        down, _ = clusters
        result = cli_testing.CliRunner().invoke(command.down, ['bobs'],
                                                input='\n')
        assert not result.exception, result.output
        assert 'Terminating 1 cluster: bobs. Proceed? [Y/n]' in result.output
        assert _names(down) == ['bobs']

    def test_old_server_is_not_narrowed(self, clusters, monkeypatch):
        monkeypatch.setattr(
            'sky.client.sdk.get_user_workspace',
            mock.MagicMock(side_effect=exceptions.APINotSupportedError('old')))
        down, _ = clusters
        result = cli_testing.CliRunner().invoke(command.down, ['-u', '-y'])
        assert not result.exception, result.output
        assert 'Skipped' not in result.output
        assert _names(down) == ['bobs', 'own', 'own-b']

    def test_server_without_auth_is_not_narrowed(self, clusters, monkeypatch):
        response = requests.Response()
        response.status_code = 401
        monkeypatch.setattr(
            'sky.client.sdk.get_user_workspace',
            mock.MagicMock(side_effect=requests.HTTPError(response=response)))
        result = cli_testing.CliRunner().invoke(command.down, ['-a', '-y'])
        assert not result.exception, result.output
        assert _names(clusters[0]) == ['own', 'own-b']

    def test_duplicate_workspace_name_stops_before_acting(
            self, clusters, monkeypatch):
        response = requests.Response()
        response.status_code = 409
        response._content = b'{"detail": "Workspace name team is not unique."}'  # pylint: disable=protected-access
        monkeypatch.setattr(
            'sky.client.sdk.get_user_workspace',
            mock.MagicMock(side_effect=requests.HTTPError(response=response)))
        down, _ = clusters
        result = cli_testing.CliRunner().invoke(command.down, ['-u', '-y'])
        assert result.exit_code == 2
        assert 'team is not unique' in result.output
        down.assert_not_called()
        response._content = b'<html>Conflict</html>'  # pylint: disable=protected-access
        result = cli_testing.CliRunner().invoke(command.down, ['-u', '-y'])
        assert result.exit_code == 2, result.output
        down.assert_not_called()

    def test_ambiguous_workspace_stops_before_acting(self, clusters,
                                                     monkeypatch):
        monkeypatch.setattr(
            'sky.client.sdk.get_user_workspace',
            mock.MagicMock(
                return_value={
                    'workspace': None,
                    'source': 'ambiguous',
                    'note': 'multiple workspaces accessible'
                }))
        down, _ = clusters
        result = cli_testing.CliRunner().invoke(command.down, ['-u', '-y'])
        assert result.exit_code == 2
        assert 'multiple workspaces accessible' in result.output
        down.assert_not_called()

    @pytest.fixture
    def stopped(self, clusters, monkeypatch):
        del clusters
        monkeypatch.setattr(f'{__name__}._CLUSTERS', [{
            **r, 'status': status_lib.ClusterStatus.STOPPED
        } for r in _CLUSTERS])
        start = mock.MagicMock(return_value='req')
        monkeypatch.setattr('sky.client.sdk.start', start)
        return start

    def test_start_all_stays_in_the_workspace(self, stopped):
        result = cli_testing.CliRunner().invoke(command.start, ['-a'],
                                                input='\n')
        assert not result.exception, result.output
        assert '[Y/n]' in result.output
        assert ('Skipped 1 cluster in other workspaces: 1 yours (own-b).'
                in result.output)
        assert _names(stopped) == ['own']

    def test_start_glob_stays_in_the_workspace_exact_name_does_not(
            self, stopped):
        result = cli_testing.CliRunner().invoke(command.start, ['own*', 'bobs'],
                                                input='\n')
        assert 'Aborted' in result.output, result.output
        assert 'other users (1): bob: bobs' in result.output
        assert '[y/N]' in result.output
        stopped.assert_not_called()
        result = cli_testing.CliRunner().invoke(command.start,
                                                ['own*', 'bobs', '-y'])
        assert not result.exception, result.output
        assert _names(stopped) == ['bobs', 'own']
        stopped.reset_mock()
        result = cli_testing.CliRunner().invoke(command.start,
                                                ['own-b', 'bob*', '-y'])
        assert not result.exception, result.output
        assert _names(stopped) == ['bobs', 'own-b']

    def test_other_workspaces_name_yours_and_count_the_rest(
            self, clusters, monkeypatch):
        up = status_lib.ClusterStatus.UP
        monkeypatch.setattr(
            f'{__name__}._CLUSTERS', _CLUSTERS + [
                _record('own-c', 'alice', 'ws-b', status=up),
                _record('own-d', 'alice', 'ws-b', status=up),
                _record('bobs-b', 'bob', 'ws-b', status=up),
            ])
        down, _ = clusters
        result = cli_testing.CliRunner().invoke(command.down, ['-u', '-y'])
        assert not result.exception, result.output
        assert ('Skipped 4 clusters in other workspaces: 3 yours (own-b, '
                'own-c and 1 more), 1 of other users. Switch the active '
                'workspace to act on them.' in result.output)
        assert 'bobs-b' not in result.output
        assert _names(down) == ['bobs', 'own']
        for args in (['-a', '-y'], ['own*', '-y'], ['-a', 'own*', '-y']):
            result = cli_testing.CliRunner().invoke(command.down, args)
            assert result.output.count(
                'Skipped 3 clusters in other workspaces: 3 yours (own-b, '
                'own-c and 1 more). Switch') == 1, (args, result.output)
        # Nothing left out, nothing said.
        result = cli_testing.CliRunner().invoke(command.down, ['bob[s]', '-y'])
        assert 'Skipped' not in result.output, result.output

    def test_cancel_all_users_defaults_to_no(self, monkeypatch):
        monkeypatch.setattr(
            'sky.client.cli.command._get_cluster_records_and_set_ssh_config',
            lambda *args, **kwargs: [dict(_CLUSTERS[0])])
        cancel = mock.MagicMock()
        monkeypatch.setattr('sky.client.sdk.cancel', cancel)
        result = cli_testing.CliRunner().invoke(command.cancel, ['own', '-u'],
                                                input='\n')
        assert 'Aborted' in result.output, result.output
        assert '[y/N]' in result.output
        cancel.assert_not_called()


class TestStorageAndVolumes:

    def test_storage_delete_all(self, monkeypatch):
        storages = [
            _record('own', 'alice'),
            _record('own-b', 'alice', 'ws-b'),
            # Predates workspaces: reachable by name only.
            _record('legacy', 'alice', None),
        ]
        monkeypatch.setattr('sky.client.sdk.storage_ls', mock.MagicMock())
        monkeypatch.setattr('sky.client.sdk.get',
                            mock.MagicMock(return_value=storages))
        delete = mock.MagicMock(return_value='req')
        monkeypatch.setattr('sky.client.sdk.storage_delete', delete)
        result = cli_testing.CliRunner().invoke(command.storage_delete,
                                                ['-a', '-y'])
        assert not result.exception, result.output
        assert _names(delete) == ['own']
        # The legacy one is in no workspace, not in another one.
        assert ('Skipped 1 storage in other workspaces: 1 yours (own-b).'
                in result.output)
        delete.reset_mock()
        result = cli_testing.CliRunner().invoke(command.storage_delete,
                                                ['legacy', '-y'])
        assert _names(delete) == ['legacy']

    def test_storage_glob_names_yours_in_other_workspaces(self, monkeypatch):
        storages = [_record('own', 'alice'), _record('own-b', 'alice', 'ws-b')]
        monkeypatch.setattr('sky.client.sdk.storage_ls', mock.MagicMock())
        monkeypatch.setattr('sky.client.sdk.get',
                            mock.MagicMock(return_value=storages))
        delete = mock.MagicMock(return_value='req')
        monkeypatch.setattr('sky.client.sdk.storage_delete', delete)
        result = cli_testing.CliRunner().invoke(command.storage_delete,
                                                ['own*', '-y'])
        assert not result.exception, result.output
        assert ('Skipped 1 storage in other workspaces: 1 yours (own-b).'
                in result.output)
        assert _names(delete) == ['own']

    def test_storage_from_a_server_without_the_field_is_not_narrowed(
            self, monkeypatch):
        # As an older server sends them: no user or workspace.
        rows = [
            dict(name=name,
                 launched_at=0,
                 store=[],
                 last_use='',
                 status='READY') for name in ('own', 'own-b')
        ]
        storages = decoders.decode_storage_ls(rows)
        monkeypatch.setattr('sky.client.sdk.storage_ls', mock.MagicMock())
        monkeypatch.setattr('sky.client.sdk.get',
                            mock.MagicMock(return_value=storages))
        delete = mock.MagicMock(return_value='req')
        monkeypatch.setattr('sky.client.sdk.storage_delete', delete)
        result = cli_testing.CliRunner().invoke(command.storage_delete,
                                                ['-a', '-y'])
        assert not result.exception, result.output
        assert _names(delete) == ['own', 'own-b']

    def test_volumes_delete_all(self, monkeypatch):
        volumes = [
            _record('own', 'alice'),
            _record('bobs', 'bob'),
            _record('own-b', 'alice', 'ws-b'),
        ]
        monkeypatch.setattr('sky.volumes.client.sdk.ls', mock.MagicMock())
        monkeypatch.setattr('sky.client.sdk.workspaces', mock.MagicMock())
        monkeypatch.setattr(
            'sky.client.sdk.get',
            mock.MagicMock(side_effect=[volumes, {}, volumes, {}]))
        delete = mock.MagicMock(return_value='req')
        monkeypatch.setattr('sky.volumes.client.sdk.delete', delete)
        result = cli_testing.CliRunner().invoke(command.volumes_delete, ['-a'],
                                                input='\n')
        assert 'other users (1): bob: bobs' in result.output, result.output
        assert ('Skipped 1 volume in other workspaces: 1 yours (own-b).'
                in result.output)
        delete.assert_not_called()
        result = cli_testing.CliRunner().invoke(command.volumes_delete, ['-a'],
                                                input='y\n')
        assert not result.exception, result.output
        assert sorted(delete.call_args.args[0]) == ['bobs', 'own']

    @pytest.mark.parametrize('owner', ['alice', 'bob'])
    @pytest.mark.parametrize('selector', [['-a'], ['h*']])
    def test_volumes_delete_bulk_warns_before_wiping_a_host_path(
            self, monkeypatch, owner, selector):
        volumes = [
            _record('hp',
                    owner,
                    type='k8s-hostpath',
                    region='ctx1',
                    config={
                        'host_path': '/mnt/data',
                        'cleanup_on_deletion': True
                    })
        ]
        monkeypatch.setattr('sky.volumes.client.sdk.ls', mock.MagicMock())
        monkeypatch.setattr('sky.client.sdk.workspaces', mock.MagicMock())
        monkeypatch.setattr('sky.client.sdk.get',
                            mock.MagicMock(side_effect=[volumes, {}]))
        monkeypatch.setattr(
            'sky.server.common.make_authenticated_request',
            lambda *a, **kw: mock.MagicMock(json=lambda: {'role': 'admin'}))
        delete = mock.MagicMock(return_value='req')
        monkeypatch.setattr('sky.volumes.client.sdk.delete', delete)
        result = cli_testing.CliRunner().invoke(command.volumes_delete,
                                                selector,
                                                input='\n')
        assert '/mnt/data on every node of ctx1' in result.output, result.output
        assert '[y/N]' in result.output, result.output
        delete.assert_not_called()


class TestManagedJobs:

    @pytest.fixture
    def queue(self, monkeypatch):
        rows = [
            dict(job_id=1, user_hash='alice-hash', workspace='ws-a'),
            # A second task of job 1.
            dict(job_id=1, user_hash='alice-hash', workspace='ws-a'),
            dict(job_id=2,
                 user_hash='bob-hash',
                 user_name='bob',
                 workspace='ws-a'),
            dict(job_id=3, user_hash='alice-hash', workspace='ws-b'),
        ]
        version = mock.Mock()
        version.v2.return_value = True
        monkeypatch.setattr('sky.client.cli.utils.get_managed_job_queue',
                            mock.MagicMock(return_value=('req', version)))
        monkeypatch.setattr('sky.client.sdk.stream_and_get',
                            mock.MagicMock(return_value=(rows, 4, {}, 4)))
        cancel = mock.MagicMock(return_value='req')
        monkeypatch.setattr('sky.jobs.cancel', cancel)
        return cancel

    def test_all_users_cancels_exactly_what_was_listed(self, queue):
        result = cli_testing.CliRunner().invoke(command.jobs_cancel, ['-u'],
                                                input='\n')
        assert 'Aborted' in result.output, result.output
        assert 'Cancelling 2 managed jobs' in result.output
        assert 'yours (1): 1' in result.output
        assert 'other users (1): bob: 2' in result.output
        queue.assert_not_called()
        result = cli_testing.CliRunner().invoke(command.jobs_cancel, ['-u'],
                                                input='y\n')
        assert not result.exception, result.output
        kwargs = queue.call_args.kwargs
        assert sorted(kwargs['job_ids']) == [1, 2]
        assert not kwargs['all'] and not kwargs['all_users']

    def test_all_names_your_jobs_in_other_workspaces(self, queue):
        result = cli_testing.CliRunner().invoke(command.jobs_cancel, ['-a'],
                                                input='\n')
        assert ('Skipped 1 managed job in other workspaces: 1 yours (3).'
                in result.output), result.output
        queue.assert_not_called()

    def test_yes_keeps_the_server_side_selection(self, queue):
        result = cli_testing.CliRunner().invoke(command.jobs_cancel,
                                                ['-u', '-y'])
        assert not result.exception, result.output
        assert queue.call_args.kwargs['all_users']


class TestServeAndPoolDown:

    @pytest.fixture
    def services(self, monkeypatch):
        records = [
            _record('own', 'alice'),
            _record('bobs', 'bob'),
            _record('own-b', 'alice', 'ws-b'),
        ]
        monkeypatch.setattr('sky.serve.status', mock.MagicMock())
        monkeypatch.setattr('sky.jobs.pool_status', mock.MagicMock())
        monkeypatch.setattr('sky.client.sdk.stream_and_get',
                            mock.MagicMock(return_value=records))
        down = mock.MagicMock(return_value='req')
        monkeypatch.setattr('sky.serve.down', down)
        return down

    def _role(self, monkeypatch, role):
        response = mock.Mock()
        response.json.return_value = {'id': 'alice-hash', 'role': role}
        monkeypatch.setattr('sky.server.common.make_authenticated_request',
                            mock.MagicMock(return_value=response))

    def test_admin_down_all_lists_others_and_enter_aborts(
            self, services, monkeypatch):
        self._role(monkeypatch, 'admin')
        result = cli_testing.CliRunner().invoke(command.serve_down, ['-a'],
                                                input='\n')
        assert 'Aborted' in result.output, result.output
        assert 'other users (1): bob: bobs' in result.output
        assert 'yours (1): own\n' in result.output
        assert ('Skipped 1 service in other workspaces: 1 yours (own-b).'
                in result.output)
        services.assert_not_called()

    def test_user_down_all_lists_own_only(self, services, monkeypatch):
        self._role(monkeypatch, 'user')
        result = cli_testing.CliRunner().invoke(command.serve_down, ['-a'],
                                                input='\n')
        assert not result.exception, result.output
        assert 'Terminating 1 service: own. Proceed? [Y/n]' in result.output
        services.assert_called_once_with(service_names=['own'],
                                         all=False,
                                         purge=False)

    def test_pattern_stays_in_the_workspace_exact_name_does_not(
            self, services, monkeypatch):
        self._role(monkeypatch, 'user')
        result = cli_testing.CliRunner().invoke(command.serve_down,
                                                ['own*', 'own-b'],
                                                input='\n')
        assert not result.exception, result.output
        assert 'Terminating 2 services: own-b, own.' in result.output
        services.assert_called_once_with(service_names=['own-b', 'own'],
                                         all=False,
                                         purge=False)

    def test_exact_name_that_matches_nothing_is_still_sent(
            self, services, monkeypatch):
        self._role(monkeypatch, 'user')
        result = cli_testing.CliRunner().invoke(command.serve_down,
                                                ['none*', 'missing'],
                                                input='\n')
        assert not result.exception, result.output
        services.assert_called_once_with(service_names=['missing'],
                                         all=False,
                                         purge=False)

    def test_label_names_the_target_workspace(self, services, monkeypatch):
        self._role(monkeypatch, 'admin')
        monkeypatch.setattr(
            'sky.client.sdk.stream_and_get',
            mock.MagicMock(return_value=[
                _record('x-b', 'bob', 'ws-b', workspace_name='team-b'),
                _record('x-a', 'bob', workspace_name='team-a'),
            ]))
        result = cli_testing.CliRunner().invoke(command.serve_down,
                                                ['x-b', 'x*'],
                                                input='\n')
        assert "in workspace 'team-a'" in result.output, result.output

    def test_listing_failure_keeps_the_plain_prompt(self, services,
                                                    monkeypatch):
        monkeypatch.setattr(
            'sky.server.common.make_authenticated_request',
            mock.MagicMock(side_effect=RuntimeError('no route')))
        result = cli_testing.CliRunner().invoke(command.serve_down, ['-a'],
                                                input='\n')
        assert not result.exception, result.output
        assert 'Terminating all services. Proceed? [Y/n]' in result.output
        services.assert_called_once_with(service_names=(),
                                         all=True,
                                         purge=False)

    def _pools(self, monkeypatch, role):
        """Pools own (alice) and bobs (bob), one running job in each."""
        self._role(monkeypatch, role)
        jobs = [
            dict(job_id=1,
                 pool='own',
                 status=mock.Mock(**{'is_terminal.return_value': False})),
            dict(job_id=2,
                 pool='bobs',
                 status=mock.Mock(**{'is_terminal.return_value': False})),
        ]
        version = mock.Mock()
        version.v2.return_value = True
        monkeypatch.setattr('sky.client.cli.utils.get_managed_job_queue',
                            mock.MagicMock(return_value=('req', version)))
        records = [
            _record('own', 'alice'),
            _record('bobs', 'bob'),
        ]
        monkeypatch.setattr(
            'sky.client.sdk.stream_and_get',
            mock.MagicMock(side_effect=lambda request_id, **kwargs: (jobs, 2, {
            }, 2) if request_id == 'req' else records))
        monkeypatch.setattr('sky.jobs.pool_status',
                            mock.MagicMock(return_value='pools'))
        cancel = mock.MagicMock(return_value='cancel-req')
        monkeypatch.setattr('sky.jobs.cancel', cancel)
        monkeypatch.setattr('sky.client.sdk.get', mock.MagicMock())
        pool_down = mock.MagicMock(return_value='down-req')
        monkeypatch.setattr('sky.jobs.pool_down', pool_down)
        # Stop the wait loop: no jobs remain after the cancel.
        jobs_after = []
        monkeypatch.setattr('sky.client.cli.command.time.sleep',
                            lambda _: jobs.clear() or jobs_after)
        return cancel, pool_down

    def test_unscoped_server_keeps_the_plain_prompt(self, services,
                                                    monkeypatch):
        self._role(monkeypatch, 'admin')
        monkeypatch.setattr(
            'sky.client.sdk.get_user_workspace',
            mock.MagicMock(side_effect=exceptions.APINotSupportedError('old')))
        result = cli_testing.CliRunner().invoke(command.serve_down, ['-a'],
                                                input='\n')
        assert not result.exception, result.output
        assert 'Terminating all services. Proceed? [Y/n]' in result.output
        services.assert_called_once_with(service_names=(),
                                         all=True,
                                         purge=False)

    def test_unknown_workspace_is_named_in_the_preview(self, services,
                                                       monkeypatch):
        self._role(monkeypatch, 'user')
        monkeypatch.setattr(
            'sky.client.sdk.stream_and_get',
            mock.MagicMock(return_value=[
                _record('own', 'alice'),
                _record('lost', 'alice', None),
            ]))
        result = cli_testing.CliRunner().invoke(command.serve_down, ['-a'],
                                                input='\n')
        assert not result.exception, result.output
        assert 'workspace is unknown or not readable' in result.output
        assert 'lost' in result.output
        services.assert_called_once_with(service_names=['own'],
                                         all=False,
                                         purge=False)

    def test_old_controller_keeps_the_plain_prompt(self, services, monkeypatch):
        self._role(monkeypatch, 'user')
        monkeypatch.setattr(
            'sky.client.sdk.stream_and_get',
            mock.MagicMock(return_value=[_record('own', 'alice', None)]))
        result = cli_testing.CliRunner().invoke(command.serve_down, ['-a'],
                                                input='\n')
        assert not result.exception, result.output
        assert 'Terminating all services. Proceed? [Y/n]' in result.output
        services.assert_called_once_with(service_names=(),
                                         all=True,
                                         purge=False)

    def test_pool_down_all_cancels_only_reached_pools_jobs(
            self, services, monkeypatch):
        cancel, pool_down = self._pools(monkeypatch, 'user')
        result = cli_testing.CliRunner().invoke(command.jobs_pool_down,
                                                ['-a', '-y'])
        assert not result.exception, result.output
        assert cancel.call_args.kwargs['job_ids'] == [1]
        pool_down.assert_called_once_with(['own'], all=False, purge=False)

    def test_pool_down_declining_the_job_question_aborts(
            self, services, monkeypatch):
        cancel, pool_down = self._pools(monkeypatch, 'user')
        result = cli_testing.CliRunner().invoke(command.jobs_pool_down, ['-a'],
                                                input='y\nn\n')
        assert 'Would you like to cancel' in result.output, result.output
        assert 'Aborted' in result.output, result.output
        cancel.assert_not_called()
        pool_down.assert_not_called()

    def test_pool_down_all_asks_about_the_pools_first(self, services,
                                                      monkeypatch):
        cancel, pool_down = self._pools(monkeypatch, 'admin')
        result = cli_testing.CliRunner().invoke(command.jobs_pool_down, ['-a'],
                                                input='\n')
        assert 'Aborted' in result.output, result.output
        assert 'other users (1): bob: bobs' in result.output
        assert 'Would you like to cancel' not in result.output
        cancel.assert_not_called()
        pool_down.assert_not_called()
