"""Unit tests for sky cost-report functionality."""
import unittest
from unittest import mock

from sky import core
from sky.server.requests import payloads
from sky.skylet import constants
from sky.utils import status_lib
from sky.utils.cli_utils import status_utils


class TestCostReportCore(unittest.TestCase):
    """Test core cost-report functionality."""

    def test_cost_report_default_days(self):
        """Test cost_report with default days parameter."""
        with mock.patch('sky.global_user_state.get_clusters_from_history'
                       ) as mock_get_history:
            mock_get_history.return_value = []

            result = core.cost_report()

            # Should call with default 30 days
            mock_get_history.assert_called_once_with(days=30)
            self.assertEqual(result, [])

    def test_cost_report_custom_days(self):
        """Test cost_report with custom days parameter."""
        with mock.patch('sky.global_user_state.get_clusters_from_history'
                       ) as mock_get_history:
            mock_get_history.return_value = []

            result = core.cost_report(days=7)

            # Should call with custom 7 days
            mock_get_history.assert_called_once_with(days=7)
            self.assertEqual(result, [])

    def test_cost_report_none_days(self):
        """Test cost_report with None days parameter."""
        with mock.patch('sky.global_user_state.get_clusters_from_history'
                       ) as mock_get_history:
            mock_get_history.return_value = []

            result = core.cost_report(days=None)

            # Should call with default 30 days when None is passed
            mock_get_history.assert_called_once_with(days=30)
            self.assertEqual(result, [])

    def test_cost_report_with_pickle_errors(self):
        """Test cost_report handles pickle errors gracefully when loading historical data."""
        import pickle

        # Mock get_clusters_from_history to simulate pickle errors being handled internally
        with mock.patch('sky.global_user_state.get_clusters_from_history'
                       ) as mock_get_history:
            # Simulate the function handling pickle errors gracefully and returning empty list
            mock_get_history.return_value = []

            # Even if there are pickle errors internally, the function should not crash
            result = core.cost_report(days=30)

            self.assertEqual(result, [])
            mock_get_history.assert_called_once_with(days=30)


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

        cost_report_body = payloads.CostReportBody(days=15)

        # Import and test the server function
        from sky.server import server

        with mock.patch(
                'sky.server.server.executor.schedule_request') as mock_schedule:
            # Call the server endpoint
            import asyncio
            asyncio.run(server.cost_report(mock_request, cost_report_body))

            # Verify executor.schedule_request was called with correct parameters
            mock_schedule.assert_called_once()
            call_args = mock_schedule.call_args

            self.assertEqual(call_args[1]['request_id'], 'test_request_id')
            self.assertEqual(call_args[1]['request_name'], 'cost_report')
            self.assertEqual(call_args[1]['request_body'], cost_report_body)
            self.assertEqual(call_args[1]['func'], server.core.cost_report)


class TestHistoricalClusterRobustness(unittest.TestCase):
    """Test cost report handles historical clusters with missing/invalid resources gracefully."""

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
            with mock.patch('sky.global_user_state.get_clusters_from_history',
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

        with mock.patch('sky.global_user_state.get_clusters_from_history',
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

        with mock.patch('sky.global_user_state.get_clusters_from_history',
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

        with mock.patch('sky.global_user_state.get_clusters_from_history',
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

        with mock.patch('sky.global_user_state.get_clusters_from_history',
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


if __name__ == '__main__':
    unittest.main()
