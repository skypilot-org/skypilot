"""Tests for loopback detection functionality."""
import unittest.mock as mock

import fastapi
import pytest
from starlette.datastructures import Headers

from sky.server.auth.loopback import _is_loopback_ip
from sky.server.auth.loopback import is_loopback_request


class TestLoopbackDetection:
    """Test cases for loopback detection functionality."""

    def test_is_loopback_ip_ipv4(self):
        """Test IPv4 loopback detection."""
        assert _is_loopback_ip('127.0.0.1')
        assert _is_loopback_ip('127.0.0.2')  # Any 127.x.x.x is loopback
        assert not _is_loopback_ip('1.1.1.1')
        assert not _is_loopback_ip('192.168.1.1')
        assert not _is_loopback_ip('10.0.0.1')

    def test_is_loopback_ip_ipv6(self):
        """Test IPv6 loopback detection."""
        assert _is_loopback_ip('::1')
        assert not _is_loopback_ip('::2')
        assert not _is_loopback_ip('2001:db8::1')

    def test_is_loopback_ip_invalid(self):
        """Test handling of invalid IP addresses."""
        assert not _is_loopback_ip('invalid-ip')
        assert not _is_loopback_ip('')
        assert not _is_loopback_ip('999.999.999.999')

    def test_is_loopback_request_client_host(self):
        """Test loopback detection via direct client IP."""
        # Mock request with loopback client
        request = mock.Mock(spec=fastapi.Request)
        request.client = mock.Mock()
        request.client.host = '127.0.0.1'
        request.headers = Headers({})

        assert is_loopback_request(request)

        # Mock request with non-loopback client
        request.client.host = '192.168.1.100'
        assert not is_loopback_request(request)

    def test_is_loopback_request_with_forwarded_headers(self):
        """Test loopback detection with forwarding headers."""
        request = mock.Mock(spec=fastapi.Request)
        request.client = mock.Mock()
        request.client.host = '127.0.0.1'

        # Loopback client with loopback forwarded IP should pass
        request.headers = Headers({'X-Forwarded-For': '127.0.0.1'})
        assert is_loopback_request(request)

        # Loopback client with external forwarded IP should be rejected
        request.headers = Headers({'X-Real-IP': '1.2.3.4'})
        assert not is_loopback_request(request)

        # X-Forwarded-For with multiple IPs - first IP is original client
        # If first IP is loopback, should pass
        request.headers = Headers({'X-Forwarded-For': '127.0.0.1, 192.168.1.1'})
        assert is_loopback_request(request)

        # If first IP is external, should be rejected
        request.headers = Headers({'X-Forwarded-For': '192.168.1.1, 127.0.0.1'})
        assert not is_loopback_request(request)

    @pytest.mark.parametrize(
        'headers_dict,should_pass',
        [
            # Headers without client IP info don't affect loopback decision
            ({
                'X-Forwarded-Proto': 'https'
            }, True),
            ({
                'X-Forwarded-Host': 'example.com'
            }, True),
            # Forwarded header (RFC 7239) - not parsed, treated as no IP info
            ({
                'Forwarded': 'for=127.0.0.1'
            }, True),
            # X-Client-IP with loopback should pass
            ({
                'X-Client-IP': '127.0.0.1'
            }, True),
            # X-Client-IP with external IP should be rejected
            ({
                'X-Client-IP': '192.168.1.1'
            }, False),
        ])
    def test_is_loopback_request_with_various_headers(self, headers_dict,
                                                      should_pass):
        """Test loopback detection with various header combinations."""
        request = mock.Mock(spec=fastapi.Request)
        request.client = mock.Mock()
        request.client.host = '127.0.0.1'

        request.headers = Headers(headers_dict)

        result = is_loopback_request(request)
        assert result == should_pass, (
            f'Headers {headers_dict}: expected {should_pass}, got {result}')

    @pytest.mark.parametrize('headers_dict', [
        {
            'x-forwarded-for': '203.0.113.1'
        },
        {
            'X-Forwarded-For': '203.0.113.1'
        },
        {
            'X-FORWARDED-FOR': '203.0.113.1'
        },
        {
            'x-real-ip': '203.0.113.1'
        },
        {
            'X-Real-IP': '203.0.113.1'
        },
    ])
    def test_is_loopback_request_rejects_external_forwarded_ip(
            self, headers_dict):
        """Test that requests forwarded from external IPs are rejected."""
        request = mock.Mock(spec=fastapi.Request)
        request.client = mock.Mock()
        request.client.host = '127.0.0.1'

        request.headers = Headers(headers_dict)
        assert not is_loopback_request(
            request), 'Should reject request with external forwarded IP'

    def test_is_loopback_request_no_client(self):
        """Test loopback detection when client is None."""
        request = mock.Mock(spec=fastapi.Request)
        request.client = None
        request.headers = Headers({})

        assert not is_loopback_request(request)
