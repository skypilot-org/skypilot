"""Unit tests for sky cost-report functionality with controllers."""
import unittest
from unittest import mock

import colorama

from sky import core
from sky.client.cli import command
from sky.utils import controller_utils
from sky.utils import status_lib
from sky.utils.cli_utils import status_utils


class TestCostReportControllers(unittest.TestCase):
    """Test cost-report functionality specifically with controllers."""

    def setUp(self):
        """Set up test fixtures."""
        self.controller_cluster_record = {
            'name': 'sky-jobs-controller-fdeaebfa',
            'status': status_lib.ClusterStatus.UP,
            'num_nodes': 1,
            'resources': mock.Mock(),
            'total_cost': 2.50,
            'launched_at': 1640995200,
            'duration': 7200,
            'cluster_hash': 'controller123',
            'usage_intervals': [(1640995200, 1641002400)],
            'user_hash': 'fdeaebfa',
            'user_name': 'zhwu',
            'workspace': 'default',
            'autostop': 10,  # 10 minutes autostop
        }
        self.controller_cluster_record[
            'resources'].instance_type = 'n1-standard-1'
        self.controller_cluster_record['resources'].cloud = mock.Mock()
        self.controller_cluster_record[
            'resources'].cloud.__str__ = lambda: 'gcp'
        self.controller_cluster_record['resources'].get_cost = mock.Mock(
            return_value=0.05)

    def test_cost_report_controller_name_fix(self):
        """Test that cost report correctly gets controller name from controller.value.name (not cluster_name)."""
        with mock.patch('sky.global_user_state.get_clusters_from_history'
                       ) as mock_get_history:
            with mock.patch('sky.utils.controller_utils.Controllers.from_name'
                           ) as mock_from_name:
                mock_get_history.return_value = [self.controller_cluster_record]

                # Mock controller object with proper name attribute
                mock_controller = mock.Mock()
                mock_controller.value.name = 'sky-jobs-controller-fdeaebfa'
                mock_from_name.return_value = mock_controller

                # This should not raise AttributeError on cluster_name
                result = core.cost_report(days=30)

                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]['name'],
                                 'sky-jobs-controller-fdeaebfa')

    def test_cost_report_with_zero_total_cost_fix(self):
        """Test that cost report sets total_cost to 0.0 (not '-') when there's an error."""
        error_cluster_record = self.controller_cluster_record.copy()
        error_cluster_record['resources'].get_cost = mock.Mock(
            side_effect=Exception("Cost calculation error"))

        with mock.patch('sky.global_user_state.get_clusters_from_history'
                       ) as mock_get_history:
            mock_get_history.return_value = [error_cluster_record]

            result = core.cost_report(days=30)

            self.assertEqual(len(result), 1)
            # Should be 0.0, not string '-'
            self.assertEqual(result[0]['total_cost'], 0.0)
            self.assertIsInstance(result[0]['total_cost'], float)

    def test_show_cost_report_table_controller_autostop_fix(self):
        """Test that show_cost_report_table correctly displays autostop for controllers."""
        mock_records = [self.controller_cluster_record]
        controller_name = 'sky-jobs-controller-fdeaebfa'

        with mock.patch('click.echo') as mock_echo:
            with mock.patch(
                    'sky.utils.log_utils.create_table') as mock_create_table:
                mock_table = mock.Mock()
                mock_create_table.return_value = mock_table

                status_utils.show_cost_report_table(
                    mock_records,
                    show_all=False,
                    controller_name=controller_name)

                # Verify that autostop information is displayed correctly
                echo_calls = [
                    str(call[0][0]) for call in mock_echo.call_args_list
                ]
                autostop_displayed = any(
                    '(will be autostopped if idle for 10min)' in call
                    for call in echo_calls)
                self.assertTrue(autostop_displayed,
                                f"Autostop not found in: {echo_calls}")

                # Verify controller name is displayed
                controller_displayed = any(
                    controller_name in call for call in echo_calls)
                self.assertTrue(controller_displayed,
                                f"Controller name not found in: {echo_calls}")

    def test_show_cost_report_table_controller_no_autostop(self):
        """Test cost report display when controller has no autostop configured."""
        no_autostop_record = self.controller_cluster_record.copy()
        no_autostop_record['autostop'] = None

        with mock.patch('click.echo') as mock_echo:
            with mock.patch(
                    'sky.utils.log_utils.create_table') as mock_create_table:
                mock_table = mock.Mock()
                mock_create_table.return_value = mock_table

                status_utils.show_cost_report_table(
                    [no_autostop_record],
                    show_all=False,
                    controller_name='sky-jobs-controller-fdeaebfa')

                # Should not display autostop information
                echo_calls = [
                    str(call[0][0]) for call in mock_echo.call_args_list
                ]
                autostop_displayed = any(
                    'autostopped' in call for call in echo_calls)
                self.assertFalse(autostop_displayed)

    def test_cost_report_cli_with_controller_filtering(self):
        """Test that cost-report CLI correctly handles controller filtering."""
        controller_record = self.controller_cluster_record.copy()
        regular_cluster_record = {
            'name': 'regular-cluster',
            'status': status_lib.ClusterStatus.UP,
            'num_nodes': 2,
            'resources': mock.Mock(),
            'total_cost': 5.00,
            'launched_at': 1640995200,
            'duration': 3600,
            'cluster_hash': 'regular123',
            'usage_intervals': [(1640995200, 1640998800)],
            'user_hash': 'user123',
            'user_name': 'testuser',
            'workspace': 'default',
        }

        with mock.patch('sky.client.sdk.cost_report') as mock_sdk_cost_report:
            with mock.patch('sky.client.sdk.get') as mock_sdk_get:
                with mock.patch(
                        'sky.utils.controller_utils.Controllers.from_name'
                ) as mock_from_name:
                    with mock.patch(
                            'sky.utils.cli_utils.status_utils.show_cost_report_table'
                    ) as mock_show_table:
                        with mock.patch(
                                'sky.utils.cli_utils.status_utils.get_total_cost_of_displayed_records'
                        ) as mock_get_total:

                            # Mock SDK returns
                            mock_sdk_cost_report.return_value = 'request_id'
                            mock_sdk_get.return_value = [
                                controller_record, regular_cluster_record
                            ]
                            mock_get_total.return_value = 7.50

                            # Mock controller lookup
                            mock_controller = mock.Mock()
                            mock_controller.value.name = 'sky-jobs-controller-fdeaebfa'
                            mock_from_name.return_value = mock_controller

                            # Test the CLI command logic by calling with a test runner
                            from click.testing import CliRunner
                            runner = CliRunner()
                            result = runner.invoke(command.cost_report,
                                                   ['--days', '7'])

                            # Verify the command completed successfully
                            self.assertEqual(result.exit_code, 0)

                            # Verify cost_report was called
                            mock_sdk_cost_report.assert_called_once_with(days=7)

                            # Verify show_cost_report_table was called
                            self.assertTrue(mock_show_table.called)

    def test_cost_report_multiple_controllers(self):
        """Test cost report with multiple different controllers."""
        jobs_controller_record = self.controller_cluster_record.copy()

        serve_controller_record = {
            'name': 'sky-serve-controller-abc123',
            'status': status_lib.ClusterStatus.UP,
            'num_nodes': 1,
            'resources': mock.Mock(),
            'total_cost': 3.75,
            'launched_at': 1640995200,
            'duration': 5400,
            'cluster_hash': 'serve123',
            'usage_intervals': [(1640995200, 1641000600)],
            'user_hash': 'abc123',
            'user_name': 'testuser',
            'workspace': 'default',
            'autostop': 15,
        }
        serve_controller_record['resources'].instance_type = 'n1-standard-2'
        serve_controller_record['resources'].cloud = mock.Mock()
        serve_controller_record['resources'].cloud.__str__ = lambda: 'gcp'
        serve_controller_record['resources'].get_cost = mock.Mock(
            return_value=0.10)

        with mock.patch('sky.global_user_state.get_clusters_from_history'
                       ) as mock_get_history:
            mock_get_history.return_value = [
                jobs_controller_record, serve_controller_record
            ]

            result = core.cost_report(days=30)

            self.assertEqual(len(result), 2)

            cluster_names = [r['name'] for r in result]
            self.assertIn('sky-jobs-controller-fdeaebfa', cluster_names)
            self.assertIn('sky-serve-controller-abc123', cluster_names)

            # Verify total costs are correctly calculated
            for record in result:
                self.assertIsInstance(record['total_cost'], (int, float))
                self.assertGreater(record['total_cost'], 0)

    def test_cost_report_controller_with_missing_attributes(self):
        """Test cost report handles controllers with missing or None attributes gracefully."""
        incomplete_controller_record = {
            'name': 'sky-jobs-controller-incomplete',
            'status': status_lib.ClusterStatus.UP,
            'num_nodes': 1,
            'resources': mock.Mock(),
            'total_cost': 1.25,
            'launched_at': 1640995200,
            'duration': 1800,
            'cluster_hash': 'incomplete123',
            'usage_intervals': [(1640995200, 1640997000)],
            'user_hash': 'incomplete',
            'user_name': 'testuser',
            'workspace': 'default',
            'autostop': None,  # No autostop configured
        }
        incomplete_controller_record['resources'].instance_type = 'unknown-type'
        incomplete_controller_record['resources'].cloud = mock.Mock()
        incomplete_controller_record['resources'].cloud.__str__ = lambda: 'aws'
        incomplete_controller_record['resources'].get_cost = mock.Mock(
            return_value=0.03)

        with mock.patch('sky.global_user_state.get_clusters_from_history'
                       ) as mock_get_history:
            mock_get_history.return_value = [incomplete_controller_record]

            # Should not raise exception
            result = core.cost_report(days=30)

            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]['name'],
                             'sky-jobs-controller-incomplete')

    def test_cost_report_helper_functions_with_controllers(self):
        """Test that cost report helper functions work correctly with controller records."""
        # Test helper functions from status_utils that are used in cost reporting

        # Test _get_resources_for_cost_report
        resources_str = status_utils._get_resources_for_cost_report(
            self.controller_cluster_record, truncate=True)
        self.assertIsInstance(resources_str, str)
        self.assertIn('1x', resources_str)  # Should show node count

        # Test _get_price_for_cost_report
        price_str = status_utils._get_price_for_cost_report(
            self.controller_cluster_record, truncate=True)
        self.assertIsInstance(price_str, str)
        self.assertIn('$', price_str)  # Should have dollar sign

        # Test _get_estimated_cost_for_cost_report
        cost_str = status_utils._get_estimated_cost_for_cost_report(
            self.controller_cluster_record, truncate=True)
        self.assertIsInstance(cost_str, str)
        self.assertEqual(cost_str, '$ 2.50')  # Should match our test data

        # Test _get_status_for_cost_report
        status_str = status_utils._get_status_for_cost_report(
            self.controller_cluster_record, truncate=True)
        self.assertIsInstance(status_str, str)
        # Should contain ANSI color codes for UP status
        self.assertIn('\x1b[', status_str)  # ANSI escape sequence

    def test_cost_report_table_formatting_with_controller(self):
        """Test that cost report table is formatted correctly for controllers."""
        with mock.patch('click.echo') as mock_echo:
            with mock.patch(
                    'sky.utils.log_utils.create_table') as mock_create_table:
                mock_table = mock.Mock()
                mock_create_table.return_value = mock_table

                status_utils.show_cost_report_table(
                    [self.controller_cluster_record],
                    show_all=True,  # Test with show_all=True
                    controller_name='sky-jobs-controller-fdeaebfa')

                # Verify table creation and display
                mock_create_table.assert_called_once()
                mock_table.add_row.assert_called_once()

                # Verify proper formatting in echo calls
                echo_calls = [
                    str(call[0][0]) for call in mock_echo.call_args_list
                ]

                # Should have controller name with proper formatting
                controller_name_found = False
                for call in echo_calls:
                    if 'sky-jobs-controller-fdeaebfa' in call:
                        # Should have ANSI color codes
                        self.assertIn(colorama.Fore.CYAN, call)
                        self.assertIn(colorama.Style.BRIGHT, call)
                        self.assertIn(colorama.Style.RESET_ALL, call)
                        controller_name_found = True
                        break

                self.assertTrue(
                    controller_name_found,
                    f"Properly formatted controller name not found in: {echo_calls}"
                )


if __name__ == '__main__':
    unittest.main()
