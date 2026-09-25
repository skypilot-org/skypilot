from starlette.requests import Request

from sky.serve import load_balancing_policies


def test_request_repr_redacts_sensitive_fields():
    request = Request({
        'type': 'http',
        'method': 'GET',
        'path': '/v1/generate',
        'query_string': (b'prompt=hello&api_key=query-secret'),
        'headers': [
            (b'authorization', b'Bearer header-secret'),
            (b'x-request-id', b'request-id'),
        ],
        'scheme': 'https',
        'server': ('localhost', 443),
        'client': ('127.0.0.1', 1234),
        'root_path': '',
        'http_version': '1.1',
    })

    result = load_balancing_policies._request_repr(request)

    assert 'header-secret' not in result
    assert 'query-secret' not in result
    assert "'authorization': '<redacted>'" in result
    assert "'x-request-id': 'request-id'" in result
    assert 'url="/v1/generate"' in result
