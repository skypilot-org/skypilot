"""Unit tests for sky cost-report functionality."""
import unittest
from unittest import mock

from sky import core
from sky.server.requests import payloads
from sky.utils.cli_utils import status_utils
from sky.utils import status_lib
from sky.skylet import constants


class TestCostReportCore(unittest.TestCase):
    """Test core cost-report functionality."""

    def test_cost_report_default_days(self):
        """Test cost_report with default days parameter."""
        with mock.patch('sky.global_user_state.get_clusters_from_history') as mock_get_history:
            mock_get_history.return_value = []
            
            result = core.cost_report()
            
            # Should call with default 30 days
            mock_get_history.assert_called_once_with(days=30)
            self.assertEqual(result, [])

    def test_cost_report_custom_days(self):
        """Test cost_report with custom days parameter."""
        with mock.patch('sky.global_user_state.get_clusters_from_history') as mock_get_history:
            mock_get_history.return_value = []
            
            result = core.cost_report(days=7)
            
            # Should call with custom 7 days
            mock_get_history.assert_called_once_with(days=7)
            self.assertEqual(result, [])

    def test_cost_report_none_days(self):
        """Test cost_report with None days parameter."""
        with mock.patch('sky.global_user_state.get_clusters_from_history') as mock_get_history:
            mock_get_history.return_value = []
            
            result = core.cost_report(days=None)
            
            # Should call with default 30 days when None is passed
            mock_get_history.assert_called_once_with(days=30)
            self.assertEqual(result, [])


class TestCostReportStatusUtils(unittest.TestCase):
    """Test cost-report status utilities."""

    def test_show_cost_report_table_with_days(self):
        """Test show_cost_report_table displays days information."""
        mock_records = []
        
        with mock.patch('click.echo') as mock_echo:
            with mock.patch('sky.utils.log_utils.create_table') as mock_create_table:
                mock_table = mock.Mock()
                mock_create_table.return_value = mock_table
                
                status_utils.show_cost_report_table(
                    mock_records, 
                    show_all=False, 
                    days=7
                )
                
                # Should display days information in header
                mock_echo.assert_called()
                echo_calls = [call[0][0] for call in mock_echo.call_args_list]
                header_with_days = any('(last 7 days)' in call for call in echo_calls)
                self.assertTrue(header_with_days, "Should display days in header")

    def test_show_cost_report_table_without_days(self):
        """Test show_cost_report_table without days information."""
        mock_records = []
        
        with mock.patch('click.echo') as mock_echo:
            with mock.patch('sky.utils.log_utils.create_table') as mock_create_table:
                mock_table = mock.Mock()
                mock_create_table.return_value = mock_table
                
                status_utils.show_cost_report_table(
                    mock_records, 
                    show_all=False, 
                    days=None
                )
                
                # Should not display days information in header
                mock_echo.assert_called()
                echo_calls = [call[0][0] for call in mock_echo.call_args_list]
                header_with_days = any('(last' in call for call in echo_calls)
                self.assertFalse(header_with_days, "Should not display days in header when None")

    def test_cost_report_helper_functions_signature(self):
        """Test that cost report helper functions have correct signatures."""
        # Test that helper functions accept truncate parameter (regression test)
        mock_record = {
            'status': status_lib.ClusterStatus.UP,
            'num_nodes': 1,
            'resources': mock.Mock(),
            'total_cost': 10.50
        }
        
        # These should not raise TypeError
        status_utils._get_status_value_for_cost_report(mock_record, truncate=True)
        status_utils._get_status_for_cost_report(mock_record, truncate=False)
        status_utils._get_resources_for_cost_report(mock_record, truncate=True)
        status_utils._get_price_for_cost_report(mock_record, truncate=False)
        status_utils._get_estimated_cost_for_cost_report(mock_record, truncate=True)


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
        
        with mock.patch('sky.server.server.executor.schedule_request') as mock_schedule:
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


class TestResourceParsingRegression(unittest.TestCase):
    """Test resource parsing improvements don't break existing functionality."""

    def test_memory_unit_parsing(self):
        """Test that new memory units are parsed correctly."""
        from sky.resources import parse_memory_resource
        
        # Test new shorter units
        self.assertEqual(parse_memory_resource('8g', 'memory'), '8')
        self.assertEqual(parse_memory_resource('16m', 'memory'), '0')  # Rounds down
        self.assertEqual(parse_memory_resource('1t', 'memory'), '1024')
        
        # Test existing units still work
        self.assertEqual(parse_memory_resource('8gb', 'memory'), '8')
        self.assertEqual(parse_memory_resource('16mb', 'memory'), '0')
        self.assertEqual(parse_memory_resource('1tb', 'memory'), '1024')

    def test_time_unit_parsing(self):
        """Test that new time units are parsed correctly."""
        from sky.skylet.constants import TIME_UNITS
        
        # Test new units exist
        self.assertIn('s', TIME_UNITS)
        self.assertIn('sec', TIME_UNITS)
        self.assertIn('min', TIME_UNITS)
        self.assertIn('hr', TIME_UNITS)
        self.assertIn('day', TIME_UNITS)
        
        # Test conversion values
        self.assertEqual(TIME_UNITS['s'], 1/60)  # seconds to minutes
        self.assertEqual(TIME_UNITS['sec'], 1/60)
        self.assertEqual(TIME_UNITS['min'], 1)
        self.assertEqual(TIME_UNITS['hr'], 60)
        self.assertEqual(TIME_UNITS['day'], 24 * 60)

    def test_autostop_time_parsing_extended(self):
        """Test autostop time parsing with new units."""
        from sky import resources
        
        # Test new second units
        r = resources.Resources(autostop='30s')
        if hasattr(r, 'autostop_config') and r.autostop_config is not None:
            self.assertEqual(r.autostop_config.idle_minutes, 1)  # Minimum 1 minute
        
        r = resources.Resources(autostop='30sec')
        if hasattr(r, 'autostop_config') and r.autostop_config is not None:
            self.assertEqual(r.autostop_config.idle_minutes, 1)
        
        # Test explicit minute units
        r = resources.Resources(autostop='5min')
        if hasattr(r, 'autostop_config') and r.autostop_config is not None:
            self.assertEqual(r.autostop_config.idle_minutes, 5)
        
        # Test hour units
        r = resources.Resources(autostop='2hr')
        if hasattr(r, 'autostop_config') and r.autostop_config is not None:
            self.assertEqual(r.autostop_config.idle_minutes, 120)
        
        # Test existing units still work
        r = resources.Resources(autostop='5m')
        if hasattr(r, 'autostop_config') and r.autostop_config is not None:
            self.assertEqual(r.autostop_config.idle_minutes, 5)
        
        r = resources.Resources(autostop='2h')
        if hasattr(r, 'autostop_config') and r.autostop_config is not None:
            self.assertEqual(r.autostop_config.idle_minutes, 120)


class TestCostReportCLI(unittest.TestCase):
    """Test cost-report CLI functionality."""

    @mock.patch('sky.client.sdk.get')
    @mock.patch('sky.client.sdk.cost_report')
    @mock.patch('sky.utils.cli_utils.status_utils.show_cost_report_table')
    @mock.patch('sky.utils.cli_utils.status_utils.get_total_cost_of_displayed_records')
    def test_cost_report_cli_function_calls(self, mock_get_total, mock_show_table,
                                          mock_sdk_cost_report, mock_sdk_get):
        """Test cost-report CLI function calls with mocking."""
        from sky.client.cli import command
        
        mock_sdk_cost_report.return_value = 'request_id'
        mock_sdk_get.return_value = []
        mock_get_total.return_value = 0.0
        
        # Test the internal logic by calling the CLI function directly
        # Use mock context to simulate different days parameter
        with mock.patch('sys.argv', ['sky', 'cost-report', '--days', '7']):
            # Mock click context
            with mock.patch('click.get_current_context') as mock_ctx:
                mock_ctx.return_value.params = {'all': False, 'days': 7}
                
                # Test that the function would call SDK with correct params
                # This tests the core logic without click dependency
                command.cost_report(all=False, days=7)
                
                mock_sdk_cost_report.assert_called_once_with(days=7)


if __name__ == '__main__':
    unittest.main()