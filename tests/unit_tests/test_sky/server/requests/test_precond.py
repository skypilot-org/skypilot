"""Unit tests for sky.server.requests.precond module."""
import unittest
from unittest import mock

from sky import exceptions
from sky.server.requests import preconditions
from sky.server.requests import requests as api_requests
from sky.utils import status_lib


class TestPrecondition(unittest.TestCase):
    """Unit tests for Precondition class."""

    def setUp(self):
        self.request_id = 'test-request'

    @mock.patch('sky.server.requests.requests.get_request_async')
    async def test_precondition_timeout(self, mock_get_request):
        """Test Precondition timeout behavior."""

        class Timeouted(preconditions.Precondition):

            async def check(self):
                return False, 'Still checking'

        mock_get_request.return_value = mock.MagicMock(
            status=api_requests.RequestStatus.PENDING)

        p = Timeouted(self.request_id, timeout=0.1)
        result = await p

        self.assertFalse(result)
        api_requests.set_request_failed.assert_called_once()
        self.assertIsInstance(api_requests.set_request_failed.call_args[0][1],
                              exceptions.RequestCancelled)

    @mock.patch('sky.server.requests.requests.get_request_async')
    async def test_precondition_cancelled(self, mock_get_request):
        """Test Precondition cancellation behavior."""

        class Canceled(preconditions.Precondition):

            async def check(self):
                return False, 'Waiting'

        mock_get_request.return_value = mock.MagicMock(
            status=api_requests.RequestStatus.CANCELLED)

        p = Canceled(self.request_id)
        result = await p

        self.assertFalse(result)

    @mock.patch('sky.server.requests.requests.get_request_async')
    async def test_precondition_check_exception(self, mock_get_request):
        """Test Precondition behavior when check raises exception."""

        class Errored(preconditions.Precondition):

            async def check(self):
                raise RuntimeError('Test error')

        mock_get_request.return_value = mock.MagicMock(
            status=api_requests.RequestStatus.PENDING)

        p = Errored(self.request_id)
        result = await p

        self.assertFalse(result)
        api_requests.set_request_failed.assert_called_once()


class TestClusterStartCompletePrecondition(unittest.TestCase):
    """Unit tests for ClusterStartCompletePrecondition class."""

    def setUp(self):
        """Set up test fixtures."""
        self.request_id = 'test-request'
        self.cluster_name = 'test-cluster'

    @mock.patch('sky.global_user_state.get_cluster_from_name')
    @mock.patch('sky.server.requests.requests.get_request_tasks')
    async def test_cluster_up(self, mock_get_tasks, mock_get_cluster):
        """Test when cluster is UP."""
        mock_get_cluster.return_value = {'status': status_lib.ClusterStatus.UP}
        mock_get_tasks.return_value = []

        p = preconditions.ClusterStartCompletePrecondition(
            self.request_id, self.cluster_name)
        met, msg = await p.check()

        self.assertTrue(met)
        self.assertIsNone(msg)
        # Should not check tasks when cluster is UP
        mock_get_tasks.assert_not_called()

    @mock.patch('sky.global_user_state.get_cluster_from_name')
    @mock.patch('sky.server.requests.requests.get_request_tasks')
    async def test_cluster_not_found(self, mock_get_tasks, mock_get_cluster):
        """Test when cluster is not found and no tasks are running."""
        mock_get_cluster.return_value = None
        mock_get_tasks.return_value = []

        p = preconditions.ClusterStartCompletePrecondition(
            self.request_id, self.cluster_name)
        met, msg = await p.check()

        self.assertTrue(met)
        self.assertIsNone(msg)

    @mock.patch('sky.global_user_state.get_cluster_from_name')
    @mock.patch('sky.server.requests.requests.get_request_tasks')
    async def test_cluster_starting(self, mock_get_tasks, mock_get_cluster):
        """Test when cluster is being started and there are tasks running."""
        mock_get_cluster.return_value = {
            'status': status_lib.ClusterStatus.INIT
        }
        mock_get_tasks.return_value = [mock.MagicMock()]

        p = preconditions.ClusterStartCompletePrecondition(
            self.request_id, self.cluster_name)
        met, msg = await p.check()

        self.assertFalse(met)
        self.assertIn('Waiting for cluster', msg)

    @mock.patch('sky.global_user_state.get_cluster_from_name')
    @mock.patch('sky.server.requests.requests.get_request_tasks')
    async def test_cluster_not_found_but_tasks_running(self, mock_get_tasks,
                                                       mock_get_cluster):
        """Test when cluster is not found but there are tasks running."""
        mock_get_cluster.return_value = None
        mock_get_tasks.return_value = [mock.MagicMock()]

        p = preconditions.ClusterStartCompletePrecondition(
            self.request_id, self.cluster_name)
        met, msg = await p.check()

        self.assertFalse(met)


if __name__ == '__main__':
    unittest.main()
