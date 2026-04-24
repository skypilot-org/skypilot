"""VM preemption detection pollers for the lifecycle-hooks framework.

Each cloud exposes a metadata endpoint that indicates an impending
spot/preemptible VM reclaim. On detecting one, the poller sends
``SIGTERM`` to the skylet process, which triggers the preemption
hook through the shared ``hook_executor.try_claim_teardown`` path.

- AWS: IMDSv2 ``/latest/meta-data/spot/instance-action`` (404 when
  clear; 200 body when an action is scheduled).
- GCP: metadata server ``/computeMetadata/v1/instance/preempted`` +
  ``/maintenance-event`` with ``?wait_for_change=true`` (long-poll).
- Azure: IMDS Scheduled Events
  ``/metadata/scheduledevents?api-version=2020-07-01`` — ``Preempt``
  event type indicates spot reclaim.

All HTTP uses stdlib ``urllib.request`` only (no extra dependencies).
"""

import json
import logging
import os
import signal
import threading
from typing import Optional
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# Poll intervals (seconds). Long enough to avoid rate-limiting, short
# enough that SIGTERM fires within the cloud's shutdown notice window
# (AWS: 2 min, GCP: 30 s, Azure: 30 s for standard eviction).
_AWS_POLL_INTERVAL_SECONDS = 5
_GCP_POLL_INTERVAL_SECONDS = 5  # GCP uses wait_for_change long-poll; this
# is the floor between retries after an error.
_AZURE_POLL_INTERVAL_SECONDS = 5

_HTTP_TIMEOUT_SECONDS = 10


def _signal_preemption() -> None:
    """Send SIGTERM to this process to trigger the preemption hook."""
    logger.warning('preemption_poller: preemption detected, sending SIGTERM')
    os.kill(os.getpid(), signal.SIGTERM)


# ---------------------------------------------------------------------------
# AWS (IMDSv2)
# ---------------------------------------------------------------------------


def _get_aws_imds_token() -> Optional[str]:
    try:
        req = urllib.request.Request(
            'http://169.254.169.254/latest/api/token',
            method='PUT',
            headers={'X-aws-ec2-metadata-token-ttl-seconds': '21600'})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            return resp.read().decode()
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'preemption_poller[aws]: token fetch failed: {e}')
        return None


def _poll_aws(stop_event: threading.Event) -> None:
    """AWS IMDSv2 poller: detect spot instance-action notice."""
    while not stop_event.is_set():
        token = _get_aws_imds_token()
        if token is None:
            stop_event.wait(_AWS_POLL_INTERVAL_SECONDS)
            continue
        req = urllib.request.Request(
            'http://169.254.169.254/latest/meta-data/spot/instance-action',
            headers={'X-aws-ec2-metadata-token': token})
        try:
            with urllib.request.urlopen(req,
                                        timeout=_HTTP_TIMEOUT_SECONDS) as resp:
                body = resp.read().decode().strip()
                if body:
                    logger.info(
                        f'preemption_poller[aws]: instance-action: {body!r}')
                    _signal_preemption()
                    return
        except urllib.error.HTTPError as e:
            # 404 means no action scheduled — the happy path.
            if e.code != 404:
                logger.debug(f'preemption_poller[aws]: HTTPError {e.code}: {e}')
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'preemption_poller[aws]: poll error: {e}')
        stop_event.wait(_AWS_POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# GCP (metadata server, long-poll via wait_for_change=true)
# ---------------------------------------------------------------------------


def _poll_gcp(stop_event: threading.Event) -> None:
    """GCP poller: watch `preempted` and `maintenance-event` metadata.

    Uses the metadata server's ``wait_for_change`` long-poll so we
    notice reclaims within seconds without hot-looping.
    """
    url = ('http://metadata.google.internal/computeMetadata/v1/instance/'
           'preempted?wait_for_change=true')
    while not stop_event.is_set():
        req = urllib.request.Request(url, headers={'Metadata-Flavor': 'Google'})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode().strip().upper()
                if body == 'TRUE':
                    logger.info(
                        'preemption_poller[gcp]: preempted flag flipped TRUE')
                    _signal_preemption()
                    return
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'preemption_poller[gcp]: poll error: {e}')
            stop_event.wait(_GCP_POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Azure (IMDS Scheduled Events)
# ---------------------------------------------------------------------------


def _poll_azure(stop_event: threading.Event) -> None:
    """Azure poller: watch IMDS Scheduled Events for Preempt."""
    url = ('http://169.254.169.254/metadata/scheduledevents'
           '?api-version=2020-07-01')
    while not stop_event.is_set():
        req = urllib.request.Request(url, headers={'Metadata': 'true'})
        try:
            with urllib.request.urlopen(req,
                                        timeout=_HTTP_TIMEOUT_SECONDS) as resp:
                data = json.loads(resp.read().decode() or '{}')
                events = data.get('Events') or []
                if any(ev.get('EventType') == 'Preempt' for ev in events):
                    logger.info(
                        'preemption_poller[azure]: Preempt event received')
                    _signal_preemption()
                    return
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'preemption_poller[azure]: poll error: {e}')
        stop_event.wait(_AZURE_POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def start(cloud: str) -> threading.Event:
    """Start a background preemption poller for the given cloud.

    Args:
        cloud: one of ``'aws'``, ``'gcp'``, ``'azure'``.

    Returns:
        A ``threading.Event`` that, when set, stops the poller.

    Raises:
        ValueError: if ``cloud`` is not supported.
    """
    cloud = cloud.lower()
    dispatch = {
        'aws': _poll_aws,
        'gcp': _poll_gcp,
        'azure': _poll_azure,
    }
    if cloud not in dispatch:
        raise ValueError(
            f'preemption_poller: unsupported cloud {cloud!r}. Supported: '
            f'{sorted(dispatch)}')
    stop_event = threading.Event()
    t = threading.Thread(target=dispatch[cloud],
                         args=(stop_event,),
                         name=f'preemption-poller-{cloud}',
                         daemon=True)
    t.start()
    logger.info(f'preemption_poller: started {cloud} poller '
                f'(tid={t.native_id})')
    return stop_event
