"""Service manager factory
"""

from sky.provision.vsphere.common import service_manager as service_manager_lib
from sky.provision.vsphere.common.service_manager import ServiceManager


class ServiceManagerFactory:
    """Factory class for getting service manager for a management node.
    """

    service_manager: Optional[ServiceManager] = None

    @classmethod
    def get_service_manager(
        cls,
        server: str,
        username: str,
        password: str,
        skip_verification: bool = False
    ) -> ServiceManager:
        """Get a connected service manager instance.

        Args:
            server: The vCenter server address.
            username: The username for authentication.
            password: The password for authentication.
            skip_verification: Whether to skip SSL certificate verification.

        Returns:
            A connected ServiceManager instance.

        Raises:
            Exception: If connection to the service manager fails.
        """
        service_manager = service_manager_lib.ServiceManager(
            server, username, password, skip_verification)
        try:
            service_manager.connect()
        except Exception as e:
            raise RuntimeError(
                f'Failed to connect to service manager at {server}: {e}'
            ) from e
        return service_manager
