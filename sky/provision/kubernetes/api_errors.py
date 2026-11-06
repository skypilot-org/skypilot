"""Classifying errors returned by the Kubernetes API server.

Each predicate here answers one question about one failed API call, and they
are not interchangeable. In particular, an error that is not transient
(`is_transient`) is not therefore permanent: a quota rejection (403) or a
conflict (409) can succeed later without anything changing on our side, while
`invalid_pod_spec_message` only matches the API server's own validation of a
manifest, which fails the same way on every attempt.

Keep new classifications of API errors here, next to the others, each with the
question it answers, so callers can pick the one that fits their decision.
"""
import json
import re
from typing import Any, Optional

from sky.adaptors import kubernetes

# An admission webhook with failurePolicy: Fail that has no ready endpoints
# (e.g. its controller is restarting) fails every create with a 500 whose
# message names the webhook.
WEBHOOK_UNAVAILABLE_PATTERN = re.compile(r'failed calling webhook "([^"]+)"')


def status_message(e: Any) -> str:
    """The message of the Status object in an API error's body.

    Falls back to the raw body text when it is not a JSON Status (e.g. an HTML
    page from a load balancer), and to '' when there is no body.
    """
    body = getattr(e, 'body', None)
    if body is None:
        return ''
    try:
        status = json.loads(body)
    except (TypeError, ValueError):
        return body.decode('utf-8', 'replace') if isinstance(
            body, bytes) else str(body)
    if not isinstance(status, dict):
        return str(body)
    return str(status.get('message', ''))


def is_transport_error(e: Exception) -> bool:
    """Whether `e` is a failure of the HTTP transport to the API server.

    urllib3 transport errors propagate raw out of the kubernetes client,
    with one exception: the client wraps `urllib3.exceptions.SSLError`
    into an `ApiException` with `status=0` (no HTTP response was
    received). An `ApiException` with a real HTTP status is an API error
    response, not a transport failure.
    """
    if isinstance(e, kubernetes.api_exception()):
        return e.status == 0
    return isinstance(e, kubernetes.urllib3_http_error())


def is_cloudflare_403(exception: Exception) -> bool:
    """Whether `exception` is a transient 403 from a CloudFlare proxy.

    CloudFlare proxy 403 errors carry CF-specific headers and are worth
    retrying, unlike real RBAC 403 errors.
    """
    if not isinstance(exception, kubernetes.api_exception()):
        return False
    if exception.status != 403:
        return False
    headers = exception.headers if hasattr(exception, 'headers') else {}
    if not headers:
        return False
    # CloudFlare errors have a CF-RAY header and/or Server: cloudflare.
    for k, v in headers.items():
        if 'cf-ray' in k.lower():
            return True
        if 'server' in k.lower() and 'cloudflare' in str(v).lower():
            return True
    return False


def is_transient(exception: Optional[BaseException]) -> bool:
    """Whether a failed Kubernetes API call may succeed if retried later.

    True for connection errors and timeouts, 429, 5xx and a CloudFlare 403.
    False for other 4xx responses and kubeconfig errors. "Not transient" is
    not "permanent": see the module docstring.
    """
    if isinstance(exception, kubernetes.max_retry_error()):
        return True
    if isinstance(exception, kubernetes.api_exception()):
        status = exception.status or 0
        return status == 429 or status >= 500 or is_cloudflare_403(exception)
    return False


def is_rbac_denial(e: Exception) -> bool:
    """Whether `e` is a 401/403 RBAC denial of the call itself.

    Note this also matches a 403 quota rejection, which is not a permissions
    problem; callers that must tell them apart should read the message.
    """
    return getattr(e, 'status', None) in (401, 403)


def unavailable_webhook(e: Any) -> Optional[str]:
    """The name of the admission webhook that could not be called, if any.

    Matches a 500 whose message says the API server failed calling a webhook
    (no ready endpoints, connection refused, ...). Such outages are often
    intermittent, so callers retry them for a while before giving up.
    """
    if getattr(e, 'status', None) != 500:
        return None
    match = WEBHOOK_UNAVAILABLE_PATTERN.search(status_message(e))
    return match.group(1) if match else None


def invalid_pod_spec_message(e: Any) -> Optional[str]:
    """The API server's message if it rejected the object as invalid.

    A 422 response with reason `Invalid` is the API server's own validation
    of the manifest (a duplicate volume name, a malformed field value, ...).
    It depends only on the manifest and the API server's version, so the same
    spec is rejected the same way on every attempt: no amount of waiting for
    capacity will get it created. Rejections by an admission webhook are not
    included: a webhook can decide on state other than the manifest.

    Returns None for any other error.
    """
    if getattr(e, 'status', None) != 422:
        return None
    try:
        body = json.loads(e.body)
    except (TypeError, ValueError):
        return None
    if not isinstance(body, dict) or body.get('reason') != 'Invalid':
        return None
    message = str(body.get('message', ''))
    if 'admission webhook' in message:
        return None
    return message
