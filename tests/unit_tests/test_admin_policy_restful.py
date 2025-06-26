"""Tests for RESTful admin policy functionality.

This test suite focuses on testing the REST-based admin policy architecture,
including serialization, transport, and integration issues that occur when
using RESTful admin policy servers with FastAPI.

Key areas tested:
- JSON/YAML serialization through REST policy servers via admin_policy_utils.apply()
- None key preservation during encoding/decoding in real usage scenarios
- FastAPI integration and request/response handling
- Server lifecycle management and port allocation
- Full policy application flow (like real SkyPilot usage)
"""
import atexit
import importlib
import os
import socket
import tempfile
import threading
import time
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import pytest
import uvicorn

import sky
from sky import admin_policy, skypilot_config
from sky.utils import admin_policy_utils, config_utils

# Function copied from test_admin_policy.py to avoid import path complexity
def _load_task_and_apply_policy(
    task: sky.Task,
    config_path: str,
    idle_minutes_to_autostop: Optional[int] = None,
) -> Tuple[sky.Dag, config_utils.Config]:
    """Apply admin policy using real SkyPilot patterns.
    
    This function is copied from tests/unit_tests/test_admin_policy.py
    to avoid import path complexity while reusing the same proven pattern.
    """
    os.environ[skypilot_config.ENV_VAR_SKYPILOT_CONFIG] = config_path
    importlib.reload(skypilot_config)
    return admin_policy_utils.apply(
        task,
        request_options=admin_policy.RequestOptions(
            cluster_name='test',
            idle_minutes_to_autostop=idle_minutes_to_autostop,
            down=False,
            dryrun=False,
        ))


def find_free_port() -> int:
    """Find a free port for the test server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


# Global registry to track active servers for cleanup
_active_servers = []


def cleanup_all_servers():
    """Cleanup function to ensure all servers are stopped."""
    for server in _active_servers:
        try:
            server.force_stop()
        except Exception:
            pass
    _active_servers.clear()


# Register cleanup function to run on exit
atexit.register(cleanup_all_servers)


@pytest.fixture
def policy_server():
    """Pytest fixture that provides a clean PolicyServer instance."""
    with PolicyServer() as server:
        # Clear any previous requests before each test
        ImageIdInspectorPolicy.received_requests.clear()
        yield server


class DoNothingPolicy(sky.AdminPolicy):
    """A no-op policy that returns the request unchanged (like the real examples)."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Returns the user request unchanged."""
        return sky.MutatedUserRequest(user_request.task,
                                      user_request.skypilot_config)


class ImageIdInspectorPolicy(sky.AdminPolicy):
    """A policy that inspects and logs what it receives for debugging."""

    received_requests = []

    @classmethod  
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Logs what the policy receives and returns it unchanged."""
        # Store what we received for inspection
        cls.received_requests.append(user_request)
        return sky.MutatedUserRequest(user_request.task, user_request.skypilot_config)


# Create FastAPI app like the real examples
app = FastAPI(title="Test Admin Policy Server", version="1.0.0")


@app.post('/')
async def apply_policy(request: Request) -> JSONResponse:
    """Apply admin policy to a user request (real implementation from examples)."""
    try:
        # Decode - this is where JSON deserialization happens (like real policy servers)
        json_data = await request.json()
        user_request = sky.UserRequest.decode(json_data)
        
        # Apply the policy
        policies = [ImageIdInspectorPolicy]
        
        for policy in policies:
            mutated_request = policy.validate_and_mutate(user_request)
            user_request.task = mutated_request.task
            user_request.skypilot_config = mutated_request.skypilot_config
            
        # Encode response - this is where JSON serialization happens
        response_data = mutated_request.encode()
        return JSONResponse(content=response_data)
        
    except Exception as e:
        import traceback
        error_msg = f"Server error: {str(e)}\nTraceback: {traceback.format_exc()}"
        return JSONResponse(content={"error": error_msg}, status_code=500)


class PolicyServer:
    """Test policy server that runs in a background thread with automatic port assignment."""
    
    def __init__(self, port=None):
        self.port = port or find_free_port()
        self.server = None
        self.thread = None
        self._started = False
        
        # Register this server for global cleanup
        _active_servers.append(self)
    
    def start(self):
        """Start the policy server in a background thread."""
        if self._started:
            return
            
        config = uvicorn.Config(
            app, 
            host="127.0.0.1", 
            port=self.port, 
            log_level="critical",  # Minimal logging for cleaner test output
            access_log=False
        )
        self.server = uvicorn.Server(config)
        
        def run_server():
            try:
                self.server.run()
            except Exception:
                # Ignore errors during shutdown
                pass
        
        self.thread = threading.Thread(target=run_server, daemon=True)
        self.thread.start()
        self._started = True
        
        # Wait for server to start and verify it's responding
        max_attempts = 10
        for _ in range(max_attempts):
            try:
                import requests
                response = requests.get(f"http://127.0.0.1:{self.port}/docs", timeout=1)
                if response.status_code in (200, 404):  # Either docs or 404 means server is up
                    break
            except (requests.exceptions.RequestException, requests.exceptions.ConnectionError):
                time.sleep(0.1)
        else:
            raise RuntimeError(f"Policy server failed to start on port {self.port}")
    
    def stop(self):
        """Stop the policy server gracefully."""
        if not self._started:
            return
            
        if self.server:
            self.server.should_exit = True
            # Give it a moment to stop gracefully
            time.sleep(0.1)
        
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        
        self._started = False
        
        # Remove from active servers
        if self in _active_servers:
            _active_servers.remove(self)
    
    def force_stop(self):
        """Force stop the server (used by cleanup)."""
        self.stop()
    
    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()


def create_test_task() -> sky.Task:
    """Create a test task with Kubernetes infra and docker image.
    
    This creates the scenario that triggers the None key serialization issue:
    - Kubernetes infrastructure (no regions, so None key in image_id)
    - Docker image specified (creates image_id mapping)
    
    Returns:
        sky.Task: A task configured to reproduce the serialization issue
    """
    # Inline YAML content instead of reading from file
    task_config = {
        'resources': {
            'infra': 'k8s',  # This creates None keys in image_id
            'image_id': 'docker:ubuntu:22.04'
        }
    }
    
    return sky.Task.from_yaml_config(task_config)


def test_none_key_serialization_through_real_policy_flow():
    """Test None key preservation through the real admin policy application flow.
    
    This test verifies that the YAML-based serialization approach correctly
    preserves None keys in image_id mappings when using the actual admin_policy_utils.apply()
    flow that real SkyPilot usage follows.
    
    The test PASSES when the serialization preserves None keys and FAILS
    when None keys are converted to string 'None' keys.
    """
    # Use context manager for automatic cleanup
    with PolicyServer() as server:
        # Clear previous requests
        ImageIdInspectorPolicy.received_requests.clear()
        
        # Create task with None key in image_id (k8s + docker scenario)
        task = create_test_task()
        resources = list(task.resources)[0]
        original_image_id = resources.image_id
        
        # Verify setup: original should have None key
        assert None in original_image_id, "Setup: Expected None key in original image_id"
        assert original_image_id[None] == 'docker:ubuntu:22.04'
        
        # Create a temporary config file with our policy URL (like real usage)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(f'admin_policy: http://127.0.0.1:{server.port}\n')
            config_path = f.name
        
        try:
            # Apply policy using the existing function from test_admin_policy.py
            dag, mutated_config = _load_task_and_apply_policy(task, config_path)
            
            # Check what the policy server actually received during the real flow
            assert len(ImageIdInspectorPolicy.received_requests) == 1
            received_request = ImageIdInspectorPolicy.received_requests[0]
            
            # Verify the request has proper structure (like real SkyPilot requests)
            assert received_request.task is not None
            assert received_request.skypilot_config is not None
            assert received_request.request_options is not None
            assert received_request.request_options.cluster_name == 'test'
            assert received_request.request_options.dryrun is False
            
            # Check the image_id preservation in the received request
            received_resources = list(received_request.task.resources)[0]
            received_image_id = received_resources.image_id
            
            # EXPECT CORRECT BEHAVIOR: Policy should receive None keys, not 'None' strings
            assert None in received_image_id, (
                f"REST POLICY BUG: Policy server received string 'None' instead of None key. "
                f"Server received keys: {list(received_image_id.keys())}. "
                f"This breaks policies that expect image_id[None] to work."
            )
            
            assert 'None' not in received_image_id, (
                f"REST POLICY BUG: None key became string 'None' in policy server. "
                f"Server received keys: {list(received_image_id.keys())}. "
                f"This corruption happens during REST policy JSON processing."
            )
            
            # Check what we get back from the full admin policy application
            mutated_task = dag.tasks[0]
            final_resources = list(mutated_task.resources)[0]
            final_image_id = final_resources.image_id
            
            # EXPECT CORRECT BEHAVIOR: Final result should have None keys preserved
            assert None in final_image_id, (
                f"ADMIN POLICY BUG: None key lost in full admin policy application. "
                f"Final keys: {list(final_image_id.keys())}. "
                f"This is the root cause of ResourcesUnavailableError with k8s+docker."
            )
            
            assert final_image_id[None] == 'docker:ubuntu:22.04', (
                f"ADMIN POLICY BUG: Cannot access image_id[None] after admin policy. "
                f"Available keys: {list(final_image_id.keys())}"
            )
            
            assert 'None' not in final_image_id, (
                f"ADMIN POLICY BUG: None key became string 'None' in final result. "
                f"Final keys: {list(final_image_id.keys())}"
            )
        finally:
            # Clean up temp file
            os.unlink(config_path)


def test_restful_policy_with_request_options():
    """Test RESTful admin policy with proper RequestOptions (like real usage)."""
    with PolicyServer() as server:
        ImageIdInspectorPolicy.received_requests.clear()
        
        # Create a test task
        task = create_test_task()
        
        # Create temporary config and apply policy using existing function
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(f'admin_policy: http://127.0.0.1:{server.port}\n')
            config_path = f.name
        
        try:
            dag, config = _load_task_and_apply_policy(task, config_path)
            
            # Verify the policy was called with proper request structure
            assert len(ImageIdInspectorPolicy.received_requests) == 1
            request = ImageIdInspectorPolicy.received_requests[0]
            
            # Check that RequestOptions were properly included (like real usage)
            assert request.request_options is not None
            assert request.request_options.cluster_name == 'test'
            assert request.request_options.idle_minutes_to_autostop is None
            assert request.request_options.down is False
            assert request.request_options.dryrun is False
            
            # Check that we got valid results back
            assert dag is not None
            assert len(dag.tasks) == 1
            assert config is not None
        finally:
            os.unlink(config_path)


def test_restful_policy_basic_functionality():
    """Test basic RESTful admin policy functionality using real patterns."""
    with PolicyServer() as server:
        ImageIdInspectorPolicy.received_requests.clear()
        
        # Create a simple test task
        task = create_test_task()
        
        # Create temporary config and apply policy using existing function
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(f'admin_policy: http://127.0.0.1:{server.port}\n')
            config_path = f.name
        
        try:
            dag, config = _load_task_and_apply_policy(task, config_path)
            
            # Check that the policy was called
            assert len(ImageIdInspectorPolicy.received_requests) == 1
            
            # Check that we got valid results (like real SkyPilot would expect)
            assert dag is not None
            assert len(dag.tasks) == 1
            assert dag.tasks[0] is not None
            assert config is not None
        finally:
            os.unlink(config_path)


if __name__ == '__main__':
    print("Testing RESTful admin policy functionality...")
    
    try:
        test_none_key_serialization_through_real_policy_flow()
        print("✓ None key serialization test (real flow) passed")
    except AssertionError as e:
        print(f"✗ None key serialization test (real flow) failed: {e}")
    except Exception as e:
        print(f"✗ None key serialization test (real flow) error: {e}")
        
    try:
        test_restful_policy_with_request_options()
        print("✓ RequestOptions test passed")
    except AssertionError as e:
        print(f"✗ RequestOptions test failed: {e}")
    except Exception as e:
        print(f"✗ RequestOptions test error: {e}")
        
    try:
        test_restful_policy_basic_functionality()
        print("✓ Basic functionality test passed")
    except AssertionError as e:
        print(f"✗ Basic functionality test failed: {e}")
    except Exception as e:
        print(f"✗ Basic functionality test error: {e}")
    
    # Cleanup any remaining servers
    cleanup_all_servers()
    
    print("\nRESTful admin policy tests completed.")
