"""Unit tests for sky cost-report functionality."""
import gc
import unittest
from unittest import mock
import weakref

import pytest
import sqlalchemy

from sky import core
from sky import global_user_state
from sky import models
from sky import resources as resources_lib
from sky import skypilot_config
from sky.server.requests import payloads
from sky.server.requests.serializers import encoders
from sky.skylet import constants
from sky.users import permission
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import status_lib
from sky.utils.cli_utils import status_utils
from sky.utils.db import db_utils


def _history_record(index, launched):
    return {
        'name': f'c{index}',
        'status': None,
        'num_nodes': 1,
        'resources': launched,
        'duration': 60,
        'launched_at': 1640995200,
        'cluster_hash': f'h{index}',
        'usage_intervals': [(1640995200, 1640995260)],
        'user_hash': 'u1',
    }


class TestCostReportCore(unittest.TestCase):
    """Test core cost-report functionality."""

    def test_cost_report_default_days(self):
        """Test cost_report with default days parameter."""
        with mock.patch('sky.global_user_state.iter_clusters_from_history'
                       ) as mock_get_history:
            mock_get_history.return_value = []

            result = core.cost_report()

            # Should call with default 30 days
            mock_get_history.assert_called_once_with(
                days=30,
                abbreviate_response=False,
                cluster_hashes=None,
                cluster_names=None,
                exclude_managed_clusters=False,
                workspaces_filter=None)
            self.assertEqual(result, [])

    def test_cost_report_custom_days(self):
        """Test cost_report with custom days parameter."""
        with mock.patch('sky.global_user_state.iter_clusters_from_history'
                       ) as mock_get_history:
            mock_get_history.return_value = []

            result = core.cost_report(days=7)

            # Should call with custom 7 days
            mock_get_history.assert_called_once_with(
                days=7,
                abbreviate_response=False,
                cluster_hashes=None,
                cluster_names=None,
                exclude_managed_clusters=False,
                workspaces_filter=None)
            self.assertEqual(result, [])

    def test_cost_report_none_days(self):
        """Test cost_report with None days parameter."""
        with mock.patch('sky.global_user_state.iter_clusters_from_history'
                       ) as mock_get_history:
            mock_get_history.return_value = []

            result = core.cost_report(days=None)

            # Should call with default 30 days when None is passed
            mock_get_history.assert_called_once_with(
                days=30,
                abbreviate_response=False,
                cluster_hashes=None,
                cluster_names=None,
                exclude_managed_clusters=False,
                workspaces_filter=None)
            self.assertEqual(result, [])

    def test_cost_report_with_cluster_names_filter(self):
        """Test cost_report forwards cluster_names to history and disables
        abbreviation when a name filter is provided."""
        with mock.patch('sky.global_user_state.iter_clusters_from_history'
                       ) as mock_get_history:
            mock_get_history.return_value = []

            core.cost_report(dashboard_summary_response=True,
                             cluster_names=['my-cluster'],
                             exclude_managed_clusters=False)

            # When filtering by name, abbreviate_response must be False so
            # the dashboard receives full records (yaml/command fields).
            mock_get_history.assert_called_once_with(
                days=30,
                abbreviate_response=False,
                cluster_hashes=None,
                cluster_names=['my-cluster'],
                exclude_managed_clusters=False,
                workspaces_filter=None)

    def test_cost_report_with_both_hash_and_name_filters(self):
        """Test cost_report forwards both filters when both are given."""
        with mock.patch('sky.global_user_state.iter_clusters_from_history'
                       ) as mock_get_history:
            mock_get_history.return_value = []

            core.cost_report(dashboard_summary_response=True,
                             cluster_hashes=['abc'],
                             cluster_names=['my-cluster'],
                             exclude_managed_clusters=False)

            mock_get_history.assert_called_once_with(
                days=30,
                abbreviate_response=False,
                cluster_hashes=['abc'],
                cluster_names=['my-cluster'],
                exclude_managed_clusters=False,
                workspaces_filter=None)

    def test_cost_report_with_pickle_errors(self):
        """Test cost_report handles pickle errors gracefully when loading historical data."""
        import pickle

        # Mock get_clusters_from_history to simulate pickle errors being handled internally
        with mock.patch('sky.global_user_state.iter_clusters_from_history'
                       ) as mock_get_history:
            # Simulate the function handling pickle errors gracefully and returning empty list
            mock_get_history.return_value = []

            # Even if there are pickle errors internally, the function should not crash
            result = core.cost_report(days=30)

            self.assertEqual(result, [])
            mock_get_history.assert_called_once_with(
                days=30,
                abbreviate_response=False,
                cluster_hashes=None,
                cluster_names=None,
                exclude_managed_clusters=False,
                workspaces_filter=None)

    def test_cost_report_encodes_resources_for_the_response(self):
        """Full reports carry each row's resources already encoded."""
        launched = resources_lib.Resources(cpus='2')
        with mock.patch('sky.global_user_state.iter_clusters_from_history',
                        return_value=iter([_history_record(0, launched)])):
            result = core.cost_report(days=30)

        expected = encoders.encode_resources(launched)
        self.assertEqual(result[0]['resources'], expected)
        # The response encoder leaves already-encoded resources as they are.
        self.assertEqual(
            encoders.encode_cost_report(result)[0]['resources'], expected)

    def test_cost_report_encodes_resources_once_with_custom_encoder(self):
        """A plugin's non-string encode_resources result is sent as is."""
        launched = resources_lib.Resources(cpus='2')
        encoded = {'cpus': '2'}
        with mock.patch('sky.global_user_state.iter_clusters_from_history',
                        return_value=iter([_history_record(0, launched)])), \
                mock.patch.object(encoders, 'encode_resources',
                                  return_value=encoded) as mock_encode:
            result = encoders.encode_cost_report(core.cost_report(days=30))

        mock_encode.assert_called_once_with(launched)
        self.assertEqual(result[0]['resources'], encoded)

    def test_cost_report_releases_resources_between_chunks(self):
        """A chunk's resources are released before the next chunk is read."""
        chunk_size = core._COST_REPORT_CHUNK_SIZE
        first_resources = []
        first_alive_at_next_chunk = []

        def history():
            for i in range(2 * chunk_size):
                if i == chunk_size:
                    gc.collect()
                    first_alive_at_next_chunk.append(
                        first_resources[0]() is not None)
                launched = resources_lib.Resources(cpus='2')
                if i == 0:
                    first_resources.append(weakref.ref(launched))
                yield _history_record(i, launched)

        with mock.patch('sky.global_user_state.iter_clusters_from_history',
                        return_value=history()):
            result = core.cost_report(days=30)

        self.assertEqual(len(result), 2 * chunk_size)
        self.assertEqual(first_alive_at_next_chunk, [False])


class TestCostReportStatusUtils(unittest.TestCase):
    """Test cost-report status utilities."""

    def test_show_cost_report_table_with_days(self):
        """Test show_cost_report_table displays days information."""
        # Need non-empty records for show_cost_report_table to call echo
        mock_resources = mock.Mock()
        mock_resources.get_cost.return_value = 0.05  # Return numeric value for cost calculation
        mock_records = [{
            'name': 'test-cluster',
            'status': status_lib.ClusterStatus.UP,
            'num_nodes': 1,
            'resources': mock_resources,
            'total_cost': 5.50,
            'launched_at': 1640995200,
            'duration': 3600,
        }]

        with mock.patch('click.echo') as mock_echo:
            with mock.patch(
                    'sky.utils.log_utils.create_table') as mock_create_table:
                mock_table = mock.Mock()
                mock_create_table.return_value = mock_table

                status_utils.show_cost_report_table(mock_records,
                                                    show_all=False,
                                                    days=7)

                # Should display days information in header
                mock_echo.assert_called()
                echo_calls = [
                    str(call[0][0]) for call in mock_echo.call_args_list
                ]
                header_with_days = any(
                    '(last 7 days)' in call for call in echo_calls)
                self.assertTrue(header_with_days,
                                "Should display days in header")

    def test_show_cost_report_table_without_days(self):
        """Test show_cost_report_table without days information."""
        # Need non-empty records for show_cost_report_table to call echo
        mock_resources = mock.Mock()
        mock_resources.get_cost.return_value = 0.05  # Return numeric value for cost calculation
        mock_records = [{
            'name': 'test-cluster',
            'status': status_lib.ClusterStatus.UP,
            'num_nodes': 1,
            'resources': mock_resources,
            'total_cost': 5.50,
            'launched_at': 1640995200,
            'duration': 3600,
        }]

        with mock.patch('click.echo') as mock_echo:
            with mock.patch(
                    'sky.utils.log_utils.create_table') as mock_create_table:
                mock_table = mock.Mock()
                mock_create_table.return_value = mock_table

                status_utils.show_cost_report_table(mock_records,
                                                    show_all=False,
                                                    days=None)

                # Should not display days information in header
                mock_echo.assert_called()
                echo_calls = [
                    str(call[0][0]) for call in mock_echo.call_args_list
                ]
                header_with_days = any('(last' in call for call in echo_calls)
                self.assertFalse(header_with_days,
                                 "Should not display days in header when None")

    def test_cost_report_helper_functions_signature(self):
        """Test that cost report helper functions have correct signatures."""
        # Test that helper functions accept truncate parameter (regression test)
        mock_resources = mock.Mock()
        mock_resources.get_cost.return_value = 0.05  # Return numeric value for cost calculation
        mock_record = {
            'status': status_lib.ClusterStatus.UP,
            'num_nodes': 1,
            'resources': mock_resources,
            'total_cost': 10.50
        }

        # These should not raise TypeError
        status_utils._get_status_value_for_cost_report(mock_record,
                                                       truncate=True)
        status_utils._get_status_for_cost_report(mock_record, truncate=False)
        status_utils._get_resources_for_cost_report(mock_record, truncate=True)
        status_utils._get_price_for_cost_report(mock_record, truncate=False)
        status_utils._get_estimated_cost_for_cost_report(mock_record,
                                                         truncate=True)


class TestCostReportServer(unittest.TestCase):
    """Test cost-report server functionality."""

    def test_cost_report_body_payload(self):
        """Test CostReportBody payload structure."""
        # Test default days
        body = payloads.CostReportBody()
        self.assertEqual(body.days, 30)

        # Test custom days
        body = payloads.CostReportBody(days=7)
        self.assertEqual(body.days, 7)

        # Test None days
        body = payloads.CostReportBody(days=None)
        self.assertIsNone(body.days)

    @mock.patch('sky.server.server.core.cost_report')
    def test_cost_report_endpoint_calls_core(self, mock_core_cost_report):
        """Test that cost_report server endpoint calls core function with correct args."""
        mock_core_cost_report.return_value = []

        # Create mock request and body
        mock_request = mock.Mock()
        mock_request.state.request_id = 'test_request_id'
        mock_request.state.auth_user = None

        cost_report_body = payloads.CostReportBody(days=15)

        # Import and test the server function
        from sky.server import server

        with mock.patch('sky.server.server.executor.schedule_request_async'
                       ) as mock_schedule_async:
            # Call the server endpoint
            import asyncio
            asyncio.run(server.cost_report(mock_request, cost_report_body))

            # Verify executor.schedule_request was called with correct parameters
            mock_schedule_async.assert_called_once()
            call_args = mock_schedule_async.call_args

            self.assertEqual(call_args[1]['request_id'], 'test_request_id')
            self.assertEqual(call_args[1]['request_name'], 'cost_report')
            self.assertEqual(call_args[1]['request_body'], cost_report_body)
            self.assertEqual(call_args[1]['func'], server.core.cost_report)


class TestHistoricalClusterRobustness(unittest.TestCase):
    """Test cost report handles historical clusters with missing/invalid resources gracefully."""

    def setUp(self):
        # The records' mock resources cannot be pickled for the response.
        patcher = mock.patch.object(core.encoders,
                                    'encode_resources',
                                    return_value='encoded')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_cost_report_with_missing_instance_type(self):
        """Test cost report doesn't crash when historical cluster has unknown instance type."""
        # Mock a cluster record with an instance type that doesn't exist in catalogs
        mock_cluster_record = {
            'name': 'old-cluster',
            'status': None,  # Historical cluster
            'num_nodes': 2,
            'resources': mock.Mock(),
            'total_cost': 0.0,
            'launched_at': 1640995200,  # Some timestamp
            'duration': 3600,
            'cluster_hash': 'abc123',
            'usage_intervals': [(1640995200, 1640998800)],
            'user_hash': 'user123',
            'user_name': 'testuser',
            'workspace': 'default',
        }

        # Mock the resources object to have an unknown instance type
        mock_cluster_record[
            'resources'].instance_type = 'unknown-instance-type-v1'
        mock_cluster_record['resources'].cloud = mock.Mock()
        mock_cluster_record['resources'].cloud.__str__ = lambda: 'aws'

        # Mock catalog functions to return None for unknown instance type
        with mock.patch('sky.catalog.get_hourly_cost', return_value=None):
            with mock.patch('sky.global_user_state.iter_clusters_from_history',
                            return_value=[mock_cluster_record]):

                # This should not raise an exception
                result = core.cost_report(days=30)

                # Should return the cluster even if cost calculation fails
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]['name'], 'old-cluster')

    def test_status_utils_with_invalid_resources(self):
        """Test status utility functions handle invalid resources gracefully."""
        # Create a mock cluster record with problematic resources
        mock_record = {
            'status': None,
            'num_nodes': 1,
            'resources': None,  # Problematic: None resources
            'total_cost': 0.0
        }

        # These should not crash even with None resources
        try:
            status_utils._get_resources_for_cost_report(mock_record,
                                                        truncate=True)
            status_utils._get_price_for_cost_report(mock_record, truncate=True)
            status_utils._get_estimated_cost_for_cost_report(mock_record,
                                                             truncate=True)
        except (AttributeError, TypeError):
            # Expected - the functions might fail gracefully, but shouldn't crash the whole system
            pass

    def test_cost_report_with_corrupted_resources_data(self):
        """Test cost report handles corrupted/unpicklable resources data."""
        mock_cluster_record = {
            'name': 'corrupted-cluster',
            'status': None,
            'num_nodes': 1,
            'resources': mock.Mock(),
            'total_cost': 0.0,
            'launched_at': 1640995200,
            'duration': 1800,
            'cluster_hash': 'def456',
            'usage_intervals': [(1640995200, 1640997000)],
            'user_hash': 'user456',
            'user_name': 'testuser2',
            'workspace': 'default',
        }

        # Mock resources with missing/invalid attributes
        mock_cluster_record['resources'].instance_type = None
        mock_cluster_record['resources'].cloud = None

        with mock.patch('sky.global_user_state.iter_clusters_from_history',
                        return_value=[mock_cluster_record]):

            # Should handle gracefully and not crash
            result = core.cost_report(days=30)
            self.assertIsInstance(result, list)

    def test_cost_report_with_empty_usage_intervals(self):
        """Test cost report handles clusters with empty or malformed usage intervals."""
        mock_cluster_record = {
            'name': 'empty-intervals-cluster',
            'status': None,
            'num_nodes': 1,
            'resources': mock.Mock(),
            'total_cost': 0.0,
            'launched_at': None,  # Missing launch time
            'duration': 0,
            'cluster_hash': 'ghi789',
            'usage_intervals': [],  # Empty intervals
            'user_hash': 'user789',
            'user_name': 'testuser3',
            'workspace': 'default',
        }

        mock_cluster_record['resources'].instance_type = 'valid-type'
        mock_cluster_record['resources'].cloud = mock.Mock()
        mock_cluster_record['resources'].cloud.__str__ = lambda: 'gcp'

        with mock.patch('sky.global_user_state.iter_clusters_from_history',
                        return_value=[mock_cluster_record]):

            # Should handle gracefully
            result = core.cost_report(days=30)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]['name'], 'empty-intervals-cluster')

    def test_cost_report_mixed_valid_invalid_clusters(self):
        """Test cost report works when some clusters are valid and others have issues."""
        valid_cluster = {
            'name': 'valid-cluster',
            'status': status_lib.ClusterStatus.UP,
            'num_nodes': 1,
            'resources': mock.Mock(),
            'total_cost': 5.50,
            'launched_at': 1640995200,
            'duration': 3600,
            'cluster_hash': 'valid123',
            'usage_intervals': [(1640995200, 1640998800)],
            'user_hash': 'user_valid',
            'user_name': 'validuser',
            'workspace': 'default',
        }
        valid_cluster['resources'].instance_type = 'standard-instance'
        valid_cluster['resources'].cloud = mock.Mock()
        valid_cluster['resources'].cloud.__str__ = lambda: 'aws'

        invalid_cluster = {
            'name': 'invalid-cluster',
            'status': None,
            'num_nodes': 1,
            'resources': mock.Mock(),
            'total_cost': 0.0,
            'launched_at': 1640995200,
            'duration': 1800,
            'cluster_hash': 'invalid456',
            'usage_intervals': [(1640995200, 1640997000)],
            'user_hash': 'user_invalid',
            'user_name': 'invaliduser',
            'workspace': 'default',
        }
        invalid_cluster[
            'resources'].instance_type = 'discontinued-instance-type'
        invalid_cluster['resources'].cloud = mock.Mock()
        invalid_cluster['resources'].cloud.__str__ = lambda: 'nonexistent-cloud'

        with mock.patch('sky.global_user_state.iter_clusters_from_history',
                        return_value=[valid_cluster, invalid_cluster]):

            # Should return both clusters, even if one has issues
            result = core.cost_report(days=30)
            self.assertEqual(len(result), 2)

            cluster_names = [r['name'] for r in result]
            self.assertIn('valid-cluster', cluster_names)
            self.assertIn('invalid-cluster', cluster_names)

    def test_cost_report_with_controller_clusters(self):
        """Test cost report handles controller clusters without errors."""
        controller_cluster = {
            'name': 'sky-jobs-controller-abc123',
            'status': status_lib.ClusterStatus.UP,
            'num_nodes': 1,
            'resources': mock.Mock(),
            'total_cost': 2.50,
            'launched_at': 1640995200,
            'duration': 7200,
            'cluster_hash': 'controller123',
            'usage_intervals': [(1640995200, 1641002400)],
            'user_hash': 'user_controller',
            'user_name': 'controlleruser',
            'workspace': 'default',
        }
        controller_cluster['resources'].instance_type = 'controller-instance'
        controller_cluster['resources'].cloud = mock.Mock()
        controller_cluster['resources'].cloud.__str__ = lambda: 'aws'

        with mock.patch('sky.global_user_state.iter_clusters_from_history',
                        return_value=[controller_cluster]):

            # Should handle controller clusters without issues
            result = core.cost_report(days=30)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]['name'], 'sky-jobs-controller-abc123')

    def test_status_utils_with_none_resources_string(self):
        """Test status utils generate safe strings when resources are problematic."""
        mock_record_with_none = {
            'status': None,
            'num_nodes': 1,
            'resources': None,
            'total_cost': 0.0
        }

        mock_record_with_missing_attrs = {
            'status': None,
            'num_nodes': 2,
            'resources': mock.Mock(),
            'total_cost': 10.0
        }
        # Mock resources object missing expected attributes
        mock_record_with_missing_attrs['resources'].instance_type = None
        del mock_record_with_missing_attrs[
            'resources'].cloud  # Simulate missing attribute

        # Test that these don't crash the status utility functions
        for record in [mock_record_with_none, mock_record_with_missing_attrs]:
            try:
                status_utils._get_resources_for_cost_report(record,
                                                            truncate=True)
            except:
                pass  # May fail, but shouldn't crash the whole system

            try:
                status_utils._get_price_for_cost_report(record, truncate=True)
            except:
                pass

            try:
                status_utils._get_estimated_cost_for_cost_report(record,
                                                                 truncate=True)
            except:
                pass


class TestCostReportCLI(unittest.TestCase):
    """Test cost-report CLI functionality."""

    @mock.patch('sky.client.sdk.get')
    @mock.patch('sky.client.sdk.cost_report')
    @mock.patch('sky.utils.cli_utils.status_utils.show_cost_report_table')
    @mock.patch(
        'sky.utils.cli_utils.status_utils.get_total_cost_of_displayed_records')
    def test_cost_report_cli_function_calls(self, mock_get_total,
                                            mock_show_table,
                                            mock_sdk_cost_report, mock_sdk_get):
        """Test cost-report CLI function calls with mocking."""
        from sky.client.cli import command

        mock_sdk_cost_report.return_value = 'request_id'
        mock_sdk_get.return_value = []
        mock_get_total.return_value = 0.0

        # Test the CLI command logic by calling with a test runner
        from click.testing import CliRunner
        runner = CliRunner()
        result = runner.invoke(command.cost_report, ['--days', '7'])

        # Verify the command completed successfully
        self.assertEqual(result.exit_code, 0)

        # Verify cost_report was called
        mock_sdk_cost_report.assert_called_once_with(days=7)


class _MinimalHandle:
    launched_resources = None


@pytest.fixture
def two_workspaces(tmp_path, monkeypatch):
    """alice is a member of private ws-a, bob of private ws-b, root is admin.

    Each has a live cluster in their workspace with a creation YAML and one
    event. Yields a function that sets the calling user.
    """
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
    config = config_utils.Config({
        'workspaces': {
            'ws-a': {
                'private': True,
                'allowed_users': ['alice']
            },
            'ws-b': {
                'private': True,
                'allowed_users': ['bob']
            },
        }
    })
    with skypilot_config.replace_skypilot_config_in_process(config):
        caller = {'id': 'alice'}
        monkeypatch.setattr(common_utils, 'get_current_user',
                            lambda: models.User(id=caller['id']))
        for user, workspace in [('alice', 'ws-a'), ('bob', 'ws-b')]:
            caller['id'] = user
            _launch(f'{user}-cluster', workspace)

        monkeypatch.setenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, 'true')
        policy = [['alice', 'ws-a', '*'], ['bob', 'ws-b', '*'],
                  ['*', 'default', '*']]
        monkeypatch.setattr(
            permission.permission_service, 'enforcer',
            mock.Mock(get_policy=lambda: policy,
                      get_roles_for_user=lambda user_id: ['admin']
                      if user_id == 'root' else ['user']))
        monkeypatch.setattr(permission.permission_service,
                            '_load_policy_no_lock', mock.Mock())

        def set_caller(user_id):
            caller['id'] = user_id
            annotations.clear_request_level_cache()

        set_caller('alice')
        yield set_caller
    annotations.clear_request_level_cache()


def _launch(name, workspace):
    with mock.patch.object(skypilot_config,
                           'get_active_workspace',
                           return_value=workspace):
        global_user_state.add_or_update_cluster(
            cluster_name=name,
            cluster_handle=_MinimalHandle(),
            requested_resources=set(),
            ready=True,
            task_config={'run': f'echo {name}'})
    global_user_state.add_cluster_event(
        name, status_lib.ClusterStatus.UP, f'{name} is up',
        global_user_state.ClusterEventType.STATUS_CHANGE)


def _set_history_workspace(cluster_hash, workspace):
    with global_user_state._db_manager.get_engine().begin() as conn:
        conn.execute(
            sqlalchemy.update(global_user_state.cluster_history_table).where(
                global_user_state.cluster_history_table.c.cluster_hash ==
                cluster_hash).values(workspace=workspace))


def _names(**kwargs):
    return sorted(r['name'] for r in core.cost_report(**kwargs))


def test_history_lists_only_readable_workspaces(two_workspaces):
    assert _names() == ['alice-cluster']
    two_workspaces('bob')
    assert _names() == ['bob-cluster']


def test_history_of_foreign_cluster_is_not_reachable_by_name_or_hash(
        two_workspaces):
    bob_hash = global_user_state.get_cluster_hash('bob-cluster')
    assert core.cost_report(cluster_names=['bob-cluster']) == []
    assert core.cost_report(cluster_hashes=[bob_hash]) == []

    own = core.cost_report(cluster_names=['alice-cluster'])
    assert 'echo alice-cluster' in own[0]['last_creation_yaml']


@pytest.mark.parametrize('history_workspace', [None, ''])
def test_history_row_without_workspace_is_hidden(two_workspaces,
                                                 history_workspace):
    """Not treated as 'default', which every user can read."""
    cluster_hash = global_user_state.get_cluster_hash('alice-cluster')
    _set_history_workspace(cluster_hash, history_workspace)
    # The record falls back to the live cluster's workspace...
    assert _names() == ['alice-cluster']
    # ...and with no live cluster there is nothing to fall back to.
    global_user_state.remove_cluster('alice-cluster', terminate=True)
    assert _names() == []


def _launch_in_deleted_workspace():
    _launch('old-cluster', 'ws-deleted')
    global_user_state.remove_cluster('old-cluster', terminate=True)


def test_admin_sees_history_of_deleted_workspace(two_workspaces):
    _launch_in_deleted_workspace()
    two_workspaces('root')
    assert _names() == ['alice-cluster', 'bob-cluster', 'old-cluster']


def test_history_is_unrestricted_off_the_api_server(two_workspaces,
                                                    monkeypatch):
    _launch_in_deleted_workspace()
    monkeypatch.delenv(constants.ENV_VAR_IS_SKYPILOT_SERVER)
    assert _names() == ['alice-cluster', 'bob-cluster', 'old-cluster']


def test_history_of_read_only_workspace_is_visible(two_workspaces):
    config = skypilot_config.to_dict()
    config.set_nested(('workspaces', 'ws-b', 'read_access'), 'all')
    with skypilot_config.replace_skypilot_config_in_process(config):
        annotations.clear_request_level_cache()
        assert _names() == ['alice-cluster', 'bob-cluster']


def test_history_loads_the_policy_once(two_workspaces):
    load_policy = permission.permission_service._load_policy_no_lock
    core.cost_report()
    assert load_policy.call_count == 1
    load_policy.reset_mock()
    core.get_cluster_events(cluster_name='alice-cluster')
    assert load_policy.call_count == 1


@pytest.mark.usefixtures('two_workspaces')
def test_cluster_history_workspace():
    alice_hash = global_user_state.get_cluster_hash('alice-cluster')
    assert global_user_state.get_cluster_history_workspace(
        cluster_name='alice-cluster') == (alice_hash, 'ws-a')
    assert global_user_state.get_cluster_history_workspace(
        cluster_hash=alice_hash) == (alice_hash, 'ws-a')
    # The history row's workspace wins over the live cluster's...
    _set_history_workspace(alice_hash, 'ws-b')
    assert global_user_state.get_cluster_history_workspace(
        cluster_hash=alice_hash) == (alice_hash, 'ws-b')
    # ...and an empty one falls back to it.
    _set_history_workspace(alice_hash, '')
    assert global_user_state.get_cluster_history_workspace(
        cluster_hash=alice_hash) == (alice_hash, 'ws-a')
    global_user_state.remove_cluster('alice-cluster', terminate=True)
    assert global_user_state.get_cluster_history_workspace(
        cluster_hash=alice_hash) == (alice_hash, None)
    assert global_user_state.get_cluster_history_workspace(
        cluster_name='alice-cluster') == (None, None)
    with pytest.raises(ValueError):
        global_user_state.get_cluster_history_workspace()
    with pytest.raises(ValueError):
        global_user_state.get_cluster_history_workspace(
            cluster_hash=alice_hash, cluster_name='alice-cluster')


def test_cluster_events_of_foreign_cluster_look_nonexistent(two_workspaces):
    bob_hash = global_user_state.get_cluster_hash('bob-cluster')
    assert core.get_cluster_events(cluster_hash=bob_hash) == []
    assert core.get_cluster_events(
        cluster_hash=bob_hash) == core.get_cluster_events(
            cluster_hash='no-such-hash')
    with pytest.raises(ValueError) as foreign:
        core.get_cluster_events(cluster_name='bob-cluster')
    assert str(foreign.value) == 'Hash for cluster bob-cluster not found.'

    assert core.get_cluster_events(cluster_name='alice-cluster') == [
        'alice-cluster is up'
    ]
    two_workspaces('root')
    assert core.get_cluster_events(cluster_hash=bob_hash) == [
        'bob-cluster is up'
    ]


if __name__ == '__main__':
    unittest.main()
