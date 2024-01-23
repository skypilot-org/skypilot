"""CLS API Client
"""

from sky.adaptors import vsphere as vsphere_adaptor


class ClsApiClient(object):
    """    This is a simplified wrapper around the Content Library APIs.
    It is used to access services exposed by Content Library Service.

    """

    def __init__(self, service_manager):

        # Client for all the services on a management node.
        self.service_manager = service_manager

        # Returns the service which provides support for generic functionality
        # which can be applied equally to all types of libraries
        self.library_service = vsphere_adaptor.get_content_client().Library(
            self.service_manager.stub_config)

        # Returns the service for managing local libraries
        self.local_library_service = vsphere_adaptor.get_content_client(
        ).LocalLibrary(self.service_manager.stub_config)

        # Returns the service for managing subscribed libraries
        self.subscribed_library_service = vsphere_adaptor.get_content_client(
        ).SubscribedLibrary(self.service_manager.stub_config)

        # Returns the service for managing library items
        self.library_item_service = vsphere_adaptor.get_library_client().Item(
            self.service_manager.stub_config)

        # Returns the service for managing sessions to update or delete content
        self.upload_service = vsphere_adaptor.get_item_client().UpdateSession(
            self.service_manager.stub_config)

        # Returns the service for managing files within an update session
        self.upload_file_service = vsphere_adaptor.get_updatesession_client(
        ).File(self.service_manager.stub_config)

        # Returns the service for managing sessions to download content
        self.download_service = vsphere_adaptor.get_item_client(
        ).DownloadSession(self.service_manager.stub_config)

        # Returns the service for managing files within a download session
        self.download_file_service = vsphere_adaptor.get_downloadsession_client(
        ).File(self.service_manager.stub_config)

        # Returns the service for deploying virtual machines from OVF library
        # items
        self.ovf_lib_item_service = vsphere_adaptor.get_ovf_client(
        ).LibraryItem(self.service_manager.stub_config)

        # Returns the service for mount and unmount of an iso file on a VM
        self.iso_service = vsphere_adaptor.get_iso_client().Image(
            self.service_manager.stub_config)

        # Returns the service for managing subscribed library items
        self.subscribed_item_service = vsphere_adaptor.get_library_client(
        ).SubscribedItem(self.service_manager.stub_config)

        # Returns the service for managing library items containing virtual
        # machine templates
        self.vmtx_service = vsphere_adaptor.get_vm_template_client(
        ).LibraryItems(self.service_manager.stub_config)

        # Returns the service for managing subscription information of
        # the subscribers of a published library.
        self.subscriptions = vsphere_adaptor.get_library_client().Subscriptions(
            self.service_manager.stub_config)

        # Creates the service that communicates with virtual machines
        self.vm_service = vsphere_adaptor.get_vcenter_client().VM(
            self.service_manager.stub_config)

        # Returns the service for managing checkouts of a library item
        # containing
        # a virtual machine template
        self.check_outs_service = vsphere_adaptor.get_library_items_client(
        ).CheckOuts(self.service_manager.stub_config)

        # Returns the service for managing the live versions of the virtual
        # machine
        # templates contained in a library item
        self.versions_service = vsphere_adaptor.get_library_items_client(
        ).Versions(self.service_manager.stub_config)

        # Returns the service for managing the history of content changes made
        # to a library item
        self.changes_service = vsphere_adaptor.get_item_client().Changes(
            self.service_manager.stub_config)

        # TODO: Add the other CLS services, eg. storage, config, type
