"""Service manager factory
"""

from sky.provision.vsphere.common.service_manager import ServiceManager


class ServiceManagerFactory(object):
    """Factory class for getting service manager for a management node.
    """

    service_manager = None

    @classmethod
    def get_service_manager(cls, server, username, password, skip_verification):
        service_manager = ServiceManager(server, username, password,
                                         skip_verification)
        service_manager.connect()
        return service_manager
