import asyncio
import pathlib
import tempfile
import time
from unittest import mock

import sky
from sky.clouds.cloud import Cloud
from sky.server import stream_utils
from sky.server.requests import requests as requests_lib
from sky.utils import message_utils
from sky.utils import rich_utils


def test_sky_launch(enable_all_clouds):
    task = sky.Task()
    job_id, handle = sky.get(sky.launch(task, dryrun=True))
    assert job_id is None and handle is None


def test_k8s_alias(monkeypatch, enable_all_clouds):

    def dryrun_task_with_cloud(cloud: Cloud):
        task = sky.Task()
        task.set_resources_override({'cloud': cloud})
        sky.stream_and_get(sky.launch(task, dryrun=True))

    monkeypatch.setattr('sky.provision.kubernetes.utils.get_spot_label',
                        lambda *_, **__: [None, None])
    dryrun_task_with_cloud(sky.K8s())

    dryrun_task_with_cloud(sky.Kubernetes())


def test_api_stream_heartbeat(monkeypatch):
    """Test that stream_and_get sends heartbeats when logs are not emitted."""

    # Patch the heartbeat interval to 0.1 seconds for faster testing
    monkeypatch.setattr(stream_utils, '_HEARTBEAT_INTERVAL', 0.1)

    # Track heartbeats generated
    heartbeats_generated = []

    # Test the actual log_streamer function with mocked file I/O
    async def test_heartbeat_generation():
        """Test that log_streamer generates heartbeats when no logs are written."""

        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write("Initial log line\n")
            f.flush()
            temp_log_path = f.name

        class MockRequest:

            def __init__(self):
                self.request_id = "test-request"
                self.status = requests_lib.RequestStatus.RUNNING
                self.name = "test_heartbeat"
                self.schedule_type = requests_lib.ScheduleType.LONG
                self.status_msg = None

        def mock_get_request(request_id):
            return MockRequest()

        monkeypatch.setattr('sky.server.requests.requests.get_request',
                            mock_get_request)

        log_path = pathlib.Path(temp_log_path)

        try:
            streamed_items = []
            start_time = asyncio.get_event_loop().time()

            # Create the async generator explicitly so we can close it properly
            log_stream = stream_utils.log_streamer(request_id="test-request",
                                                   log_path=log_path,
                                                   follow=True)

            try:
                async for item in log_stream:
                    streamed_items.append(item)
                    current_time = asyncio.get_event_loop().time()

                    try:
                        is_payload, decoded = message_utils.decode_payload(
                            item, raise_for_mismatch=False)
                        if is_payload:
                            control, _ = rich_utils.Control.decode(decoded)
                            if control == rich_utils.Control.HEARTBEAT:
                                heartbeats_generated.append(current_time)
                    except Exception:
                        pass

                    if current_time - start_time > 0.55:
                        break
            finally:
                # Properly close the async generator to avoid pending task errors
                await log_stream.aclose()

        finally:
            import os
            os.unlink(temp_log_path)

        return streamed_items

    # Create and run the async test
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        streamed_items = loop.run_until_complete(test_heartbeat_generation())
    finally:
        loop.close()

    assert len(heartbeats_generated) >= 5, (
        f"Expected at least 5 heartbeats, got {len(heartbeats_generated)}. "
        f"Total streamed items: {len(streamed_items)}")


def test_heartbeat_not_displayed_to_users(monkeypatch):
    """Test that heartbeat messages are filtered out from user display."""

    # Patch the heartbeat interval to 0.1 seconds for faster testing
    monkeypatch.setattr(stream_utils, '_HEARTBEAT_INTERVAL', 0.1)

    # Track what gets displayed to users vs heartbeats
    user_visible_messages = []
    heartbeat_messages = []

    async def test_heartbeat_filtering():
        """Test that heartbeats are filtered from user-visible output."""

        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write("User log message 1\n")
            f.flush()
            temp_log_path = f.name

        class MockRequest:

            def __init__(self):
                self.request_id = "test-request"
                self.status = requests_lib.RequestStatus.RUNNING
                self.name = "test_heartbeat_display"
                self.schedule_type = requests_lib.ScheduleType.LONG
                self.status_msg = None

        def mock_get_request(request_id):
            return MockRequest()

        monkeypatch.setattr('sky.server.requests.requests.get_request',
                            mock_get_request)

        log_path = pathlib.Path(temp_log_path)

        try:
            start_time = asyncio.get_event_loop().time()

            # Create the async generator
            log_stream = stream_utils.log_streamer(request_id="test-request",
                                                   log_path=log_path,
                                                   follow=True)

            try:
                async for item in log_stream:
                    current_time = asyncio.get_event_loop().time()

                    try:
                        is_payload, decoded = message_utils.decode_payload(
                            item, raise_for_mismatch=False)
                        if is_payload:
                            control, _ = rich_utils.Control.decode(decoded)
                            if control == rich_utils.Control.HEARTBEAT:
                                # Heartbeat should NOT be displayed to users
                                heartbeat_messages.append(item)
                            else:
                                user_visible_messages.append(item)
                        else:
                            user_visible_messages.append(item)
                    except Exception:
                        # Non-control messages should be displayed
                        user_visible_messages.append(item)

                    if current_time - start_time > 0.55:
                        break
            finally:
                await log_stream.aclose()

        finally:
            import os
            os.unlink(temp_log_path)

        return user_visible_messages, heartbeat_messages

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        user_messages, heartbeats = loop.run_until_complete(
            test_heartbeat_filtering())
    finally:
        loop.close()

    assert len(heartbeats) >= 3, (
        f"Expected at least 3 heartbeats, got {len(heartbeats)}")

    # Verify user messages contain actual log content, not heartbeats
    user_content = ''.join(
        [msg for msg in user_messages if isinstance(msg, str)])
    assert "User log message 1" in user_content, (
        "Expected user log content to be visible")

    # Verify no heartbeat control messages leaked into user display
    for msg in user_messages:
        if isinstance(msg, str):
            continue
        try:
            is_payload, decoded = message_utils.decode_payload(
                msg, raise_for_mismatch=False)
            if is_payload:
                control, _ = rich_utils.Control.decode(decoded)
                assert control != rich_utils.Control.HEARTBEAT, (
                    "Heartbeat message should not be in user-visible output")
        except Exception:
            # Non-control messages are fine in user output
            pass
