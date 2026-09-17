"""Service Manager
"""
from typing import Any, Optional

from sky.adaptors import vsphere as vsphere_adaptor
from sky.provision.vsphere.common import vapiconnect
from sky.provision.vsphere.common.ssl_helper import get_unverified_context


class ServiceManager:
    """Manages Vim and vAPI services on a management node."""

    def __init__(
        self,
        server: str,
        username: str,
        password: str,
        skip_verification: bool = False
    ) -> None:
        """Initialize the ServiceManager.

        Args:
            server: The vCenter server URL.
            username: Username for authentication.
            password: Password for authentication.
            skip_verification: Whether to skip SSL certificate verification.
        """
        self.server_url: str = server
        self.username: str = username
        self.password: str = password
        self.skip_verification: bool = skip_verification
        self.vapi_url: Optional[str] = None
        self.vim_url: Optional[str] = None
        self.session: Optional[Any] = None
        self.session_id: Optional[str] = None
        self.stub_config: Optional[Any] = None
        self.si: Optional[Any] = None
        self.content: Optional[Any] = None
        self.vim_uuid: Optional[str] = None

    def connect(self) -> None:
        """Connect to vCenter and retrieve service content.

        Raises:
            RuntimeError: If connection fails.
        """
        # Connect to vAPI Endpoint on vCenter Server system
        self.stub_config = vapiconnect.connect(
            host=self.server_url,
            user=self.username,
            pwd=self.password,
            skip_verification=self.skip_verification,
        )

        # Connect to VIM API Endpoint on vCenter Server system
        context: Optional[Any] = None
        if self.skip_verification:
            context = get_unverified_context()
        self.si = vsphere_adaptor.get_pyvim_connect().SmartConnect(
            host=self.server_url,
            user=self.username,
            pwd=self.password,
            sslContext=context,
        )
        if self.si is None:
            raise RuntimeError(f'Failed to connect to vCenter at {self.server_url}')

        # Retrieve the service content
        self.content = self.si.RetrieveContent()
        if self.content is None:
            raise RuntimeError('Failed to retrieve vCenter service content')
        self.vim_uuid = self.content.about.instanceUuid

    def disconnect(self) -> None:
        """Disconnect from vCenter and cleanup resources."""
        # print('disconnecting the session')
        if self.stub_config is not None:
            vapiconnect.logout(self.stub_config)
        if self.si is not None:
            vsphere_adaptor.get_pyvim_connect().Disconnect(self.si)
