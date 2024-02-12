"""Service manager factory
"""

from sky.provision.vsphere.common import service_manager as service_manager_lib


class ServiceManagerFactory(object):
    """Factory class for getting service manager for a management node.
    """

    service_manager = None

    @classmethod
    def get_service_manager(cls, server, username, password, skip_verification):
        service_manager = service_manager_lib.ServiceManager(
            server, username, password, skip_verification)
        service_manager.connect()
        return service_manager
