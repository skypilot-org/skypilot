"""Test for sky.utils.timeline."""

import os
from unittest import mock

import pytest

from sky.utils import timeline


def test_save_timeline():

    @timeline.event("test_save_timeline")
    def test_save_timeline():
        pass

    with mock.patch('sky.utils.timeline._get_events_file_path',
                    return_value='/tmp/sky/timeline.json'):
        test_save_timeline()
        # one entry for function entry and
        # one entry for function exit
        assert len(timeline._events) == 2
        timeline.save_timeline()
        # after saving, the events should be empty
        assert len(timeline._events) == 0
        os.remove('/tmp/sky/timeline.json')

    with mock.patch('sky.utils.timeline._get_events_file_path',
                    return_value=None):
        test_save_timeline()
        # no timeline file path, so no events should be recorded
        assert len(timeline._events) == 0
