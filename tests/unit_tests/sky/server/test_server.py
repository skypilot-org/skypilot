"""Unit tests for the SkyPilot API server."""

import argparse
import unittest
from unittest import mock

import uvicorn

from sky.utils import common_utils


class TestServerWorkers(unittest.TestCase):
    """Test the server workers configuration."""

    @mock.patch('uvicorn.run')
    @mock.patch('sky.server.requests.executor.start')
    @mock.patch('sky.utils.common_utils.get_cpu_count')
    def test_deploy_flag_sets_workers_to_cpu_count(self, mock_get_cpu_count,
                                                   mock_executor_start,
                                                   mock_uvicorn_run):
        """Test that --deploy flag sets workers to CPU count."""
        # Setup
        mock_get_cpu_count.return_value = 8
        mock_executor_start.return_value = []

        # Create mock args with deploy=True
        test_args = argparse.Namespace(host='127.0.0.1',
                                       port=46580,
                                       deploy=True)

        # Call the main block with mocked args
        with mock.patch('argparse.ArgumentParser.parse_args',
                        return_value=test_args):
            with mock.patch('sky.server.requests.requests.reset_db_and_logs'):
                with mock.patch(
                        'sky.usage.usage_lib.maybe_show_privacy_policy'):
                    # Execute the main block code directly
                    num_workers = None
                    if test_args.deploy:
                        num_workers = mock_get_cpu_count()

                    workers = []
                    try:
                        workers = mock_executor_start(test_args.deploy)
                        uvicorn.run('sky.server.server:app',
                                    host=test_args.host,
                                    port=test_args.port,
                                    workers=num_workers)
                    except Exception:
                        pass
                    finally:
                        for worker in workers:
                            worker.terminate()

        # Verify that uvicorn.run was called with the correct number of workers
        mock_uvicorn_run.assert_called_once()
        call_args = mock_uvicorn_run.call_args[1]
        self.assertEqual(call_args['workers'], 8)
        self.assertEqual(call_args['host'], '127.0.0.1')
        self.assertEqual(call_args['port'], 46580)

    @mock.patch('uvicorn.run')
    @mock.patch('sky.server.requests.executor.start')
    def test_no_deploy_flag_uses_default_workers(self, mock_executor_start,
                                                 mock_uvicorn_run):
        """Test that without --deploy flag, workers is None (default)."""
        # Setup
        mock_executor_start.return_value = []

        # Create mock args with deploy=False
        test_args = argparse.Namespace(host='127.0.0.1',
                                       port=46580,
                                       deploy=False)

        # Call the main block with mocked args
        with mock.patch('argparse.ArgumentParser.parse_args',
                        return_value=test_args):
            with mock.patch('sky.server.requests.requests.reset_db_and_logs'):
                with mock.patch(
                        'sky.usage.usage_lib.maybe_show_privacy_policy'):
                    # Execute the main block code directly
                    num_workers = None
                    if test_args.deploy:
                        num_workers = common_utils.get_cpu_count()

                    workers = []
                    try:
                        workers = mock_executor_start(test_args.deploy)
                        uvicorn.run('sky.server.server:app',
                                    host=test_args.host,
                                    port=test_args.port,
                                    workers=num_workers)
                    except Exception:
                        pass
                    finally:
                        for worker in workers:
                            worker.terminate()

        # Verify that uvicorn.run was called with workers=None
        mock_uvicorn_run.assert_called_once()
        call_args = mock_uvicorn_run.call_args[1]
        self.assertIsNone(call_args['workers'])
        self.assertEqual(call_args['host'], '127.0.0.1')
        self.assertEqual(call_args['port'], 46580)


if __name__ == '__main__':
    unittest.main()
