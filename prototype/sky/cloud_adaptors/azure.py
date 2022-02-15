"""Azure cli adaptor"""

# pylint: disable=import-outside-toplevel


def get_subscription_id() -> str:
    """Get the default subscription id."""
    from azure.common import credentials
    return credentials.get_cli_profile().get_subscription_id()
