"""SSL Helper
"""
import ssl
import typing

from sky.adaptors import common as adaptors_common

if typing.TYPE_CHECKING:
    import requests
else:
    requests = adaptors_common.LazyImport('requests')


def get_unverified_context():
    """    Get an unverified ssl context. Used to disable the server certificate
    verification.
    @return: unverified ssl context.
    """
    context = None
    if hasattr(ssl, '_create_unverified_context'):
        context = ssl._create_unverified_context()  # pylint: disable=protected-access
    return context


def get_unverified_session(insecure: bool = False):
    """    Get a requests session.
    Certificate verification is enabled by default.
    Set insecure=True only for explicit debug usage.
    @return: a requests session.
    """
    session = requests.session()
    session.verify = not insecure
    if insecure:
        requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
    return session
