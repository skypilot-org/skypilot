import time
import unittest
from unittest import mock

from sky.utils import log_utils


class TestDurationUtils(unittest.TestCase):

    @mock.patch('time.time', return_value=1700000000)
    def test_human_duration_since(self, mock_time):
        """Tests human_duration_since with the current implementation logic."""
        del mock_time  # Unused.

        # Test seconds (duration < 60*2 = 120 seconds)
        self.assertEqual(log_utils.human_duration(1700000000 - 30), '30s')
        self.assertEqual(log_utils.human_duration(1700000000 - 119), '119s')

        # Test minutes with seconds (minutes < 10)
        self.assertEqual(log_utils.human_duration(1700000000 - (5 * 60 + 10)),
                         '5m10s')
        self.assertEqual(log_utils.human_duration(1700000000 - (9 * 60)), '9m')
        self.assertEqual(log_utils.human_duration(1700000000 - (9 * 60 + 30)),
                         '9m30s')

        # Test minutes only (10 <= minutes < 60*3 = 180)
        self.assertEqual(log_utils.human_duration(1700000000 - (10 * 60)),
                         '10m')
        self.assertEqual(log_utils.human_duration(1700000000 - (179 * 60)),
                         '179m')

        # Test hours with minutes (hours < 8)
        self.assertEqual(
            log_utils.human_duration(1700000000 - (3 * 3600 + 15 * 60)),
            '3h15m')
        self.assertEqual(log_utils.human_duration(1700000000 - (7 * 3600)),
                         '7h')
        self.assertEqual(
            log_utils.human_duration(1700000000 - (7 * 3600 + 30 * 60)),
            '7h30m')

        # Test hours only (8 <= hours < 48)
        self.assertEqual(log_utils.human_duration(1700000000 - (8 * 3600)),
                         '8h')
        self.assertEqual(log_utils.human_duration(1700000000 - (47 * 3600)),
                         '47h')

        # Test days with hours (48 <= hours < 24*8 = 192)
        self.assertEqual(log_utils.human_duration(1700000000 - (48 * 3600)),
                         '2d')
        self.assertEqual(log_utils.human_duration(1700000000 - (72 * 3600)),
                         '3d')
        self.assertEqual(
            log_utils.human_duration(1700000000 - (72 * 3600 + 6 * 3600)),
            '3d6h')

        # Test days only (192 <= hours < 24*365*2 = 17520)
        self.assertEqual(log_utils.human_duration(1700000000 - (192 * 3600)),
                         '8d')
        self.assertEqual(log_utils.human_duration(1700000000 - (17519 * 3600)),
                         '729d')

        # Test years with days (17520 <= hours < 24*365*8 = 70080)
        self.assertEqual(log_utils.human_duration(1700000000 - (17520 * 3600)),
                         '2y')
        self.assertEqual(
            log_utils.human_duration(1700000000 - (17520 * 3600 + 24 * 3600)),
            '2y1d')
        self.assertEqual(log_utils.human_duration(1700000000 - (70079 * 3600)),
                         '7y364d')

        # Test years only (hours >= 70080)
        self.assertEqual(log_utils.human_duration(1700000000 - (70080 * 3600)),
                         '8y')

        # Test edge cases
        self.assertEqual(log_utils.human_duration(1700000000), '0s')
        self.assertEqual(log_utils.human_duration(1700000000 + 100), '0s')
        self.assertEqual(log_utils.human_duration(0), '0s')
        self.assertEqual(log_utils.human_duration(-100), '0s')
        self.assertEqual(log_utils.human_duration(None), '0s')
        self.assertEqual(log_utils.human_duration(1700000000, 1700000000), '0s')
        self.assertEqual(log_utils.human_duration(1700000000, 1700000001), '1s')
