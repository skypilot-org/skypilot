"""Azure cli adaptor"""

# pylint: disable=import-outside-toplevel
from functools import wraps

azure = None


def import_package(func):

    @wraps(func)
    def wrapper(*args, **kwargs):
        global azure
        if azure is None:
            try:
                import azure as _azure
                azure = _azure
            except ImportError:
                raise ImportError('Fail to import dependencies for Azure.'
                                  'See README for how to install it.') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def get_subscription_id() -> str:
    """Get the default subscription id."""
    from azure.common import credentials
    return credentials.get_cli_profile().get_subscription_id()
