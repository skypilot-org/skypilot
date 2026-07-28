"""Usage analytics for gaining insights into how SkyPilot is used.

Each event is a fire-and-forget request sent from client-side code paths
only, so it reflects the machine actually invoking the command. Sends no
PII: only a short command identifier and the SkyPilot version. Independent
of sky/usage/usage_lib.py, the existing usage collection.
"""
import functools
import inspect
import os
import threading
from typing import Callable, Sequence, TypeVar
import urllib.parse
import urllib.request

from typing_extensions import ParamSpec

_SCARF_ENDPOINT = 'https://ossapi.skypilot.ai/sky-launch'
_TIMEOUT_SECONDS = 2

T = TypeVar('T')
P = ParamSpec('P')


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
        # Analytics must never break a command.
        pass


def ping(command: str) -> None:
    if _opted_out():
        return
    # Local import avoids a circular import at module load.
    import sky  # pylint: disable=import-outside-toplevel
    params = {'command': command, 'version': sky.__version__}
    threading.Thread(target=_send, args=(params,), daemon=True).start()


# Records `command` when the decorated client-side function is called.
# `skip_if_any` names arguments whose truthiness suppresses the event, e.g.
# dryrun or controller-initiated invocations.
def track(
    command: str, skip_if_any: Sequence[str] = ()
) -> Callable[[Callable[P, T]], Callable[P, T]]:

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        sig = inspect.signature(func)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            try:
                skip = False
                if skip_if_any:
                    bound = sig.bind_partial(*args, **kwargs)
                    bound.apply_defaults()
                    skip = any(
                        bound.arguments.get(name) for name in skip_if_any)
                if not skip:
                    ping(command)
            except Exception:  # pylint: disable=broad-except
                # Analytics must never break a command.
                pass
            return func(*args, **kwargs)

        return wrapper

    return decorator
