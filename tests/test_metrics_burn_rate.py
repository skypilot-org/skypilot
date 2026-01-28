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


class TestBurnRateCollector(unittest.TestCase):

    def _make_cluster(self,
                      status_name,
                      cost=None,
                      include_handle=True,
                      include_resources=True):
        """Create a cluster dict similar to what global_user_state.get_clusters() returns."""
        status = mock.Mock()
        status.name = status_name

        cluster = {'status': status}

        if not include_handle:
            cluster['handle'] = None
            return cluster

        handle = mock.Mock()

        if not include_resources:
            handle.launched_resources = None
            cluster['handle'] = handle
            return cluster

        launched_resources = mock.Mock()
        if cost is not None:
            launched_resources.get_cost.return_value = cost
        handle.launched_resources = launched_resources

        cluster['handle'] = handle
        return cluster

    @mock.patch('sky.server.metrics.global_user_state.get_clusters')
    def test_burn_rate_sums_only_up_clusters(self, mock_get_clusters):
        # Two valid UP clusters with hourly costs 1.5 and 5.5 -> total 7.0
        c1 = self._make_cluster('UP', cost=1.5)
        c2 = self._make_cluster('UP', cost=5.5)

        # These should be ignored
        stopped = self._make_cluster('STOPPED', cost=999.0)
        no_handle = self._make_cluster('UP', cost=999.0, include_handle=False)
        no_resources = self._make_cluster('UP',
                                          cost=999.0,
                                          include_resources=False)

        mock_get_clusters.return_value = [
            c1, c2, stopped, no_handle, no_resources
        ]

        collector = metrics.BurnRateCollector()
        collected = list(collector.collect())

        self.assertEqual(len(collected), 1)
        family = collected[0]
        self.assertEqual(family.name, 'sky_apiserver_total_burn_rate_dollars')

        # Find the single emitted sample
        self.assertEqual(len(family.samples), 1)
        sample = family.samples[0]

        self.assertEqual(sample.labels['type'], 'local_clusters')
        self.assertAlmostEqual(sample.value, 7.0)

        # Ensure get_cost() called correctly for the two UP clusters only
        c1['handle'].launched_resources.get_cost.assert_called_with(
            metrics._COST_TIME_HORIZON_SECONDS)
        c2['handle'].launched_resources.get_cost.assert_called_with(
            metrics._COST_TIME_HORIZON_SECONDS)

        self.assertFalse(stopped['handle'].launched_resources.get_cost.called)

    @mock.patch('sky.server.metrics.time.time')
    @mock.patch('sky.server.metrics.global_user_state.get_clusters')
    def test_burn_rate_cache_respects_ttl(self, mock_get_clusters, mock_time):
        # Return one UP cluster costing 1.0
        c1 = self._make_cluster('UP', cost=1.0)
        mock_get_clusters.return_value = [c1]

        collector = metrics.BurnRateCollector()

        # 1st scrape at t=1000 -> computes
        # 2nd scrape at t=1001 -> should use cache
        # 3rd scrape at t=1000 + TTL + 1 -> recompute
        mock_time.side_effect = [
            1000.0,
            1001.0,
            1000.0 + metrics._BURN_RATE_UPDATE_INTERVAL_SECONDS + 1.0,
        ]

        list(collector.collect())
        list(collector.collect())
        list(collector.collect())

        # Should compute twice total (first and third), not on the second
        self.assertEqual(mock_get_clusters.call_count, 2)


if __name__ == '__main__':
    unittest.main()
