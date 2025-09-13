"""Vapi Connect
"""

import typing

from urllib3.exceptions import InsecureRequestWarning

from sky.adaptors import common as adaptors_common
from sky.adaptors import vsphere as vsphere_adaptor

if typing.TYPE_CHECKING:
    import requests
else:
    requests = adaptors_common.LazyImport('requests')


def get_jsonrpc_endpoint_url(host):
    # The URL for the stub requests are made against the /api HTTP endpoint
    # of the vCenter system.
    return 'https://{}/api'.format(host)


def connect(host,
            user,
            pwd,
            skip_verification=False,
            cert_path=None,
            suppress_warning=True):
    """Create an authenticated stub configuration object
    that can be used to issue
    requests against vCenter.

    Returns a stub_config that stores the session identifier that can be used
    to issue authenticated requests against vCenter.
    """
    host_url = get_jsonrpc_endpoint_url(host)

    session = requests.Session()
    if skip_verification:
        session = create_unverified_session(session, suppress_warning)
    elif cert_path:
        session.verify = cert_path
    connector = vsphere_adaptor.get_vapi_connect().get_requests_connector(
        session=session, url=host_url)
    stub_config = vsphere_adaptor.get_factories(
    ).StubConfigurationFactory.new_std_configuration(connector)

    return login(stub_config, user, pwd)


def login(stub_config, user, pwd):
    """Create an authenticated session with vCenter.

    Returns a stub_config that stores the session identifier that can be used
    to issue authenticated requests against vCenter.
    """
    # Pass user credentials (user/password) in the security context to
    # authenticate.
    user_password_security_context = vsphere_adaptor.get_user_password(
    ).create_user_password_security_context(user, pwd)
    stub_config.connector.set_security_context(user_password_security_context)

    # Create the stub for the session service and login by creating a session.
    session_svc = vsphere_adaptor.get_cis_client().Session(stub_config)
    session_id = session_svc.create()

    # Successful authentication.  Store the session identifier in the security
    # context of the stub and use that for all subsequent remote requests
    session_security_context = vsphere_adaptor.get_security_session(
    ).create_session_security_context(session_id)
    stub_config.connector.set_security_context(session_security_context)

    return stub_config


def logout(stub_config):
    """Delete session with vCenter.
    """
    if stub_config:
        session_svc = vsphere_adaptor.get_cis_client().Session(stub_config)
        session_svc.delete()


def create_unverified_session(session, suppress_warning=True):
    """Create a unverified session to disable the server certificate
    verification.
    This is not recommended in production code.
    """
    session.verify = False
    if suppress_warning:
        # Suppress unverified https request warnings
        requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
    return session
