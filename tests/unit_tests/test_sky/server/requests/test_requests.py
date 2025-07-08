"""Unit tests for sky.server.requests.requests module."""
import tempfile
import time
from unittest import mock
import unittest.mock

import pytest
import sqlalchemy

from sky.server.requests import payloads
from sky.server.requests import requests
from sky.server.requests.requests import RequestStatus
from sky.server.requests.requests import ScheduleType
from sky.utils import db_utils


def dummy():
    return None


class TestRequestsCRUD:
    """Test CRUD operations for requests with both SQLite and PostgreSQL."""

    def setup_method(self):
        """Setup test database for each test method."""
        # Reset the global database engine to ensure isolation
        requests._SQLALCHEMY_ENGINE = None

        # Create a fresh in-memory SQLite database for each test
        import os
        import tempfile

        # Create a temporary file for SQLite database
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix='.db')
        os.close(
            self.temp_db_fd)  # Close the file descriptor, SQLite will reopen it

        # Patch the database path to use our temporary database
        self.db_path_patcher = mock.patch(
            'sky.server.requests.requests.server_constants.API_SERVER_REQUEST_DB_PATH',
            self.temp_db_path)
        self.db_path_patcher.start()

        # Initialize the database
        requests.initialize_and_get_db()

    def teardown_method(self):
        """Clean up after each test method."""
        # Stop the patcher
        if hasattr(self, 'db_path_patcher'):
            self.db_path_patcher.stop()

        # Reset the global database engine
        requests._SQLALCHEMY_ENGINE = None

        # Clean up the temporary database file
        if hasattr(self, 'temp_db_path'):
            import os
            try:
                os.unlink(self.temp_db_path)
            except (OSError, FileNotFoundError):
                pass  # File might not exist or already deleted

    def create_test_request(self,
                            request_id='test-request-1',
                            should_retry=False) -> requests.Request:
        """Create a test request for testing."""
        return requests.Request(request_id=request_id,
                                name='test-request',
                                entrypoint=dummy,
                                request_body=payloads.RequestBody(),
                                status=RequestStatus.PENDING,
                                created_at=time.time(),
                                user_id='test-user',
                                schedule_type=ScheduleType.LONG,
                                cluster_name='test-cluster',
                                status_msg='Test status',
                                should_retry=should_retry,
                                host_uuid='test-host-uuid')

    def test_create_request(self):
        """Test creating a new request."""
        request = self.create_test_request()

        # Create the request
        assert requests.create_if_not_exists(request) is True

        # Verify it was created
        retrieved = requests.get_request('test-request-1')
        assert retrieved is not None
        assert retrieved.request_id == 'test-request-1'
        assert retrieved.name == 'test-request'
        assert retrieved.status == RequestStatus.PENDING
        assert retrieved.user_id == 'test-user'
        assert retrieved.should_retry is False
        assert retrieved.host_uuid == 'test-host-uuid'

        # Try to create the same request again - should return False
        assert requests.create_if_not_exists(request) is False

    def test_get_request_nonexistent(self):
        """Test getting a non-existent request."""
        result = requests.get_request('nonexistent-request')
        assert result is None

    def test_update_request(self):
        """Test updating an existing request."""
        request = self.create_test_request()
        requests.create_if_not_exists(request)

        # Update the request
        with requests.update_request('test-request-1') as req:
            assert req is not None
            req.status = RequestStatus.RUNNING
            req.status_msg = 'Updated status'
            req.should_retry = True

        # Verify the update
        updated = requests.get_request('test-request-1')
        assert updated is not None
        assert updated.status == RequestStatus.RUNNING
        assert updated.status_msg == 'Updated status'
        assert updated.should_retry is True

    def test_get_request_tasks_filtering(self):
        """Test filtering requests by various criteria."""
        # Create multiple requests
        request1 = self.create_test_request('req-1', should_retry=False)
        request1.status = RequestStatus.PENDING
        request1.cluster_name = 'cluster-1'
        request1.user_id = 'user-1'

        request2 = self.create_test_request('req-2', should_retry=True)
        request2.status = RequestStatus.RUNNING
        request2.cluster_name = 'cluster-2'
        request2.user_id = 'user-2'

        request3 = self.create_test_request('req-3', should_retry=False)
        request3.status = RequestStatus.SUCCEEDED
        request3.cluster_name = 'cluster-1'
        request3.user_id = 'user-1'

        requests.create_if_not_exists(request1)
        requests.create_if_not_exists(request2)
        requests.create_if_not_exists(request3)

        # Test filtering by status
        pending_requests = requests.get_request_tasks(
            status=[RequestStatus.PENDING])
        assert len(pending_requests) == 1
        assert pending_requests[0].request_id == 'req-1'

        # Test filtering by cluster name
        cluster1_requests = requests.get_request_tasks(
            cluster_names=['cluster-1'])
        assert len(cluster1_requests) == 2
        cluster1_ids = {req.request_id for req in cluster1_requests}
        assert cluster1_ids == {'req-1', 'req-3'}

        # Test filtering by user ID
        user1_requests = requests.get_request_tasks(user_id='user-1')
        assert len(user1_requests) == 2
        user1_ids = {req.request_id for req in user1_requests}
        assert user1_ids == {'req-1', 'req-3'}

        # Test exclude request names
        excluded_requests = requests.get_request_tasks(
            exclude_request_names=['test-request'])
        assert len(excluded_requests) == 0  # All have name 'test-request'

        # Test include request names
        included_requests = requests.get_request_tasks(
            include_request_names=['test-request'])
        assert len(included_requests) == 3

    def test_boolean_preservation(self):
        """Test that boolean should_retry is properly preserved."""
        request_true = self.create_test_request('req-true', should_retry=True)
        request_false = self.create_test_request('req-false',
                                                 should_retry=False)

        requests.create_if_not_exists(request_true)
        requests.create_if_not_exists(request_false)

        # Test encoding preserves bool type
        payload_true = request_true.encode()
        payload_false = request_false.encode()

        assert isinstance(payload_true.should_retry, bool)
        assert isinstance(payload_false.should_retry, bool)
        assert payload_true.should_retry is True
        assert payload_false.should_retry is False

        # Test decoding preserves bool type
        decoded_true = requests.Request.decode(payload_true)
        decoded_false = requests.Request.decode(payload_false)

        assert isinstance(decoded_true.should_retry, bool)
        assert isinstance(decoded_false.should_retry, bool)
        assert decoded_true.should_retry is True
        assert decoded_false.should_retry is False

        # Test database roundtrip
        retrieved_true = requests.get_request('req-true')
        retrieved_false = requests.get_request('req-false')

        assert retrieved_true.should_retry is True
        assert retrieved_false.should_retry is False

    def test_error_field_persistence(self):
        """Test that error field is properly stored and retrieved."""
        request = self.create_test_request()
        requests.create_if_not_exists(request)

        # Test setting an error
        try:
            raise ValueError('Test error message')
        except ValueError as e:
            requests.set_request_failed('test-request-1', e)

        # Get the request and verify error was stored correctly
        updated_request = requests.get_request('test-request-1')
        assert updated_request is not None
        assert updated_request.error is not None

        # Test that encode produces string for error field (as expected by payload)
        payload = updated_request.encode()
        assert isinstance(payload.error, str)

        # Test that decode can read the string
        decoded_request = requests.Request.decode(payload)
        assert decoded_request.error is not None
        assert isinstance(decoded_request.error, dict)

        # Verify the error details are preserved
        error_info = decoded_request.get_error()
        assert error_info is not None
        assert error_info['type'] == 'ValueError'
        assert error_info['message'] == 'Test error message'

    def test_set_request_failed(self):
        """Test setting a request as failed."""
        request = self.create_test_request()
        requests.create_if_not_exists(request)

        try:
            raise ValueError('Boom!')
        except ValueError as e:
            requests.set_request_failed('test-request-1', e)

        # Get the updated request
        updated_request = requests.get_request('test-request-1')

        # Verify the request was updated correctly
        assert updated_request is not None
        assert updated_request.status == RequestStatus.FAILED

        # Verify the error was set correctly
        error = updated_request.get_error()
        assert error is not None
        assert error['type'] == 'ValueError'
        assert error['message'] == 'Boom!'
        assert error['object'] is not None

    def test_set_request_succeeded(self):
        """Test setting a request as succeeded."""
        request = self.create_test_request()
        requests.create_if_not_exists(request)

        test_result = {'status': 'success', 'data': 'test_data'}
        requests.set_request_succeeded('test-request-1', test_result)

        # Get the updated request
        updated_request = requests.get_request('test-request-1')

        # Verify the request was updated correctly
        assert updated_request is not None
        assert updated_request.status == RequestStatus.SUCCEEDED

        # Note: We can't easily test the return value without mocking
        # the encoder/decoder, but we can verify it was set
        assert updated_request.return_value is not None

    def test_set_request_cancelled(self):
        """Test setting a request as cancelled."""
        request = self.create_test_request()
        requests.create_if_not_exists(request)

        requests.set_request_cancelled('test-request-1')

        # Get the updated request
        updated_request = requests.get_request('test-request-1')

        # Verify the request was updated correctly
        assert updated_request is not None
        assert updated_request.status == RequestStatus.CANCELLED

    def test_get_latest_request_id(self):
        """Test getting the latest request ID."""
        # Initially no requests
        assert requests.get_latest_request_id() is None

        # Create requests with different timestamps
        request1 = self.create_test_request('req-1')
        request1.created_at = 1000.0

        request2 = self.create_test_request('req-2')
        request2.created_at = 2000.0

        request3 = self.create_test_request('req-3')
        request3.created_at = 1500.0

        requests.create_if_not_exists(request1)
        requests.create_if_not_exists(request2)
        requests.create_if_not_exists(request3)

        # Should return the request with the latest timestamp
        latest_id = requests.get_latest_request_id()
        assert latest_id == 'req-2'

    def test_set_request_failed_nonexistent_request(self):
        """Test setting a non-existent request as failed."""
        # Try to set a non-existent request as failed
        with pytest.raises(AssertionError):
            requests.set_request_failed('nonexistent-request',
                                        ValueError('Test error'))

    def test_host_field_persistence(self):
        """Test that the host field is properly stored and retrieved."""
        request = self.create_test_request()
        request.host_uuid = 'api-server-host.example.com'

        requests.create_if_not_exists(request)

        retrieved = requests.get_request('test-request-1')
        assert retrieved is not None
        assert retrieved.host_uuid == 'api-server-host.example.com'

    def test_upsert_operations_sqlite(self):
        """Test upsert operations work correctly with SQLite."""
        # This test verifies our SQLite INSERT OR REPLACE works
        request = self.create_test_request()

        # First insert
        requests.create_if_not_exists(request)

        # Update via direct upsert (simulating what happens internally)
        request.status = RequestStatus.RUNNING
        request.status_msg = 'Updated via upsert'

        # This should update, not create a new record
        requests._add_or_update_request_no_lock(request)

        # Verify only one record exists and it's updated
        all_requests = requests.get_request_tasks()
        assert len(all_requests) == 1
        assert all_requests[0].status == RequestStatus.RUNNING
        assert all_requests[0].status_msg == 'Updated via upsert'


@mock.patch.dict('os.environ', {'IS_SKYPILOT_SERVER': '1'})
@mock.patch('sky.skypilot_config.get_nested')
class TestPostgreSQLSupport:
    """Test PostgreSQL-specific functionality."""

    def test_postgresql_engine_creation(self, mock_get_nested):
        """Test that PostgreSQL engine is created when configured."""
        mock_get_nested.return_value = 'postgresql://user:pass@localhost/test'

        with mock.patch.object(requests, '_SQLALCHEMY_ENGINE', None):
            with mock.patch('sqlalchemy.create_engine') as mock_create_engine:
                with mock.patch.object(requests, 'create_table'):
                    requests.initialize_and_get_db()

                    mock_create_engine.assert_called_once_with(
                        'postgresql://user:pass@localhost/test')

    def test_dialect_detection_postgresql(self, mock_get_nested):
        """Test dialect detection for PostgreSQL."""
        mock_get_nested.return_value = 'postgresql://user:pass@localhost/test'

        # Mock PostgreSQL engine
        mock_engine = mock.Mock()
        mock_engine.dialect.name = db_utils.SQLAlchemyDialect.POSTGRESQL.value

        with mock.patch.object(requests, '_SQLALCHEMY_ENGINE', mock_engine):
            with mock.patch('sqlalchemy.orm.Session') as mock_session:
                with mock.patch(
                        'sky.server.requests.requests.postgresql') as mock_pg:
                    with mock.patch('sky.server.requests.requests.sqlite'):
                        request = requests.Request(
                            request_id='test',
                            name='test',
                            entrypoint=dummy,
                            request_body=payloads.RequestBody(),
                            status=RequestStatus.PENDING,
                            created_at=time.time(),
                            user_id='test-user')

                        # Mock the insert function
                        mock_insert_func = mock.Mock()
                        mock_pg.insert.return_value = mock_insert_func
                        mock_insert_func.on_conflict_do_update.return_value = mock.Mock(
                        )

                        requests._add_or_update_request_no_lock(request)

                        # Verify PostgreSQL insert function was used
                        mock_pg.insert.assert_called_once()

    def test_dialect_detection_sqlite(self, mock_get_nested):
        """Test dialect detection for SQLite."""
        mock_get_nested.return_value = None  # Use SQLite

        # Mock SQLite engine
        mock_engine = mock.Mock()
        mock_engine.dialect.name = db_utils.SQLAlchemyDialect.SQLITE.value

        with mock.patch.object(requests, '_SQLALCHEMY_ENGINE', mock_engine):
            with mock.patch('sqlalchemy.orm.Session') as mock_session:
                with mock.patch(
                        'sky.server.requests.requests.sqlite') as mock_sqlite:
                    with mock.patch('sky.server.requests.requests.postgresql'):
                        request = requests.Request(
                            request_id='test',
                            name='test',
                            entrypoint=dummy,
                            request_body=payloads.RequestBody(),
                            status=RequestStatus.PENDING,
                            created_at=time.time(),
                            user_id='test-user')

                        # Mock the insert function
                        mock_insert_func = mock.Mock()
                        mock_sqlite.insert.return_value = mock_insert_func
                        mock_insert_func.prefix_with.return_value = mock.Mock()

                        requests._add_or_update_request_no_lock(request)

                        # Verify SQLite insert function was used
                        mock_sqlite.insert.assert_called_once()
