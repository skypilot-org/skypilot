import functools
import inspect
import os
import threading
from typing import Callable, Optional
import urllib.parse
import urllib.request

_SCARF_ENDPOINT = 'https://ossapi.skypilot.ai/sky-launch'
_TIMEOUT_SECONDS = 2


def _opted_out() -> bool:
    if os.environ.get('SKYPILOT_DISABLE_USAGE_COLLECTION') == '1':
        return True
    # DO_NOT_TRACK and SCARF_NO_ANALYTICS are opt-outs: any value other than
    # unset or '0' opts out (so e.g. DO_NOT_TRACK=0 still allows tracking).
    if os.environ.get('DO_NOT_TRACK', '0') not in ('', '0'):
        return True
    if os.environ.get('SCARF_NO_ANALYTICS', '0') not in ('', '0'):
        return True
    return False


def _send(params: dict) -> None:
    try:
        url = _SCARF_ENDPOINT + '?' + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS):
            pass
    except Exception:  # pylint: disable=broad-except
        # Telemetry must never break a command.
        pass


def ping(command: str) -> None:
    if _opted_out():
        return
    # Local import avoids a circular import at module load.
    import sky  # pylint: disable=import-outside-toplevel
    params = {'command': command, 'version': sky.__version__}
    threading.Thread(target=_send, args=(params,), daemon=True).start()


def track(command: str,
          skip: Optional[Callable[[dict], bool]] = None) -> Callable:

    def decorator(func: Callable) -> Callable:
        sig = inspect.signature(func)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                fire = True
                if skip is not None:
                    bound = sig.bind_partial(*args, **kwargs)
                    bound.apply_defaults()
                    if skip(bound.arguments):
                        fire = False
                if fire:
                    ping(command)
            except Exception:  # pylint: disable=broad-except
                # Telemetry must never break a command.
                pass
            return func(*args, **kwargs)

        return wrapper

    return decorator
