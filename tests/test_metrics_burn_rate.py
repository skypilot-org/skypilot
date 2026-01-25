#
# Copyright 2023 SkyPilot Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Unit tests for SkyPilot API server metrics."""
import unittest
from unittest import mock

from sky.server import metrics


class TestBurnRateMetric(unittest.TestCase):
    """Tests for the burn rate metric calculation."""

    def setUp(self):
        if hasattr(metrics, 'SKY_APISERVER_TOTAL_BURN_RATE'):
            metrics.SKY_APISERVER_TOTAL_BURN_RATE.set(0.0)

    @mock.patch('sky.server.metrics.global_user_state.get_clusters')
    def test_burn_rate_logic(self, mock_get_clusters):
        """Test burn rate calculation with mixed cluster states."""

        def create_mock_cluster(status_str, instance_cost=0.0, gpu_cost=0.0):
            mock_status = mock.Mock()
            mock_status.name = status_str

            mock_cloud = mock.Mock()
            mock_cloud.instance_type_to_hourly_cost.return_value = instance_cost
            mock_cloud.accelerators_to_hourly_cost.return_value = gpu_cost

            mock_resources = mock.Mock()
            mock_resources.cloud = mock_cloud
            mock_resources.accelerators = {'GPU': 1} if gpu_cost > 0.0 else None

            mock_handle = mock.Mock()
            mock_handle.launched_resources = mock_resources

            return {'status': mock_status, 'handle': mock_handle}

        cluster_a = create_mock_cluster('UP', instance_cost=1.50)

        cluster_b = create_mock_cluster('UP', instance_cost=2.00, gpu_cost=3.50)

        cluster_c = create_mock_cluster('STOPPED', instance_cost=10.00)

        cluster_d = create_mock_cluster('INIT', instance_cost=5.00)

        mock_get_clusters.return_value = [
            cluster_a, cluster_b, cluster_c, cluster_d
        ]

        metrics.update_burn_rate_metric()

        metric_value = metrics.SKY_APISERVER_TOTAL_BURN_RATE.collect(
        )[0].samples[0].value

        print(f'\nTest Result: Expected 7.0, Got {metric_value}')
        self.assertEqual(metric_value, 7.0)


if __name__ == '__main__':
    unittest.main()
