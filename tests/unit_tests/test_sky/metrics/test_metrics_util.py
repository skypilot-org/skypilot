"""Unit tests for sky.metrics.utils."""
import subprocess
from unittest import mock

import pytest

from sky.metrics import utils


def test_start_svc_port_forward_terminates_on_exception():
    """Test subprocess is terminated when exception occurs."""
    mock_process = mock.MagicMock(spec=subprocess.Popen)
    mock_process.poll.return_value = None
    mock_process.stdout = mock.MagicMock()
    mock_process.stdout.fileno.return_value = 1

    with mock.patch('subprocess.Popen',
                    return_value=mock_process), \
         mock.patch('time.time', side_effect=[0, 1, 2]), \
         mock.patch('select.select',
                    side_effect=Exception('Test error')), \
         mock.patch('time.sleep'):

        with pytest.raises(Exception, match='Test error'):
            utils.start_svc_port_forward(context='test-context',
                                         namespace='test-ns',
                                         service='test-svc',
                                         service_port=8080)

        # Verify subprocess was terminated
        mock_process.terminate.assert_called_once()
        mock_process.wait.assert_called()


def test_start_svc_port_forward_terminates_on_timeout():
    """Test subprocess is terminated when no local port found."""
    mock_process = mock.MagicMock(spec=subprocess.Popen)
    mock_process.poll.return_value = None
    mock_process.stdout = mock.MagicMock()
    mock_process.stdout.fileno.return_value = 1

    # Simulate timeout by advancing time past the timeout threshold
    with mock.patch('subprocess.Popen',
                    return_value=mock_process), \
         mock.patch('time.time', side_effect=[0] + [11] * 10), \
         mock.patch('select.select', return_value=([], [], [])), \
         mock.patch('time.sleep'):

        with pytest.raises(RuntimeError, match='Failed to extract local port'):
            utils.start_svc_port_forward(context='test-context',
                                         namespace='test-ns',
                                         service='test-svc',
                                         service_port=8080)

        # Verify subprocess was terminated
        mock_process.terminate.assert_called_once()
        mock_process.wait.assert_called()
