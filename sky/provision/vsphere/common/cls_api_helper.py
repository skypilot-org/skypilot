"""CLS API Helper
"""

import os
import ssl
import time
import urllib.request as urllib2

from sky import sky_logging
from sky.adaptors import vsphere as vsphere_adaptor
from sky.provision.vsphere.common.cls_api_client import ClsApiClient
from sky.provision.vsphere.common.id_generator import generate_random_uuid
from sky.provision.vsphere.common.vim_utils import get_obj_by_mo_id

logger = sky_logging.init_logger(__name__)


class ClsApiHelper(object):
    """Helper class to perform commonly used operations
    using Content Library API.
    """

    ISO_FILE_RELATIVE_DIR = '../resources/isoImages/'
    PLAIN_OVF_RELATIVE_DIR = '../resources/plainVmTemplate'
    SIMPLE_OVF_RELATIVE_DIR = '../resources/simpleVmTemplate'

    def __init__(self, cls_api_client: ClsApiClient, skip_verification: bool):
        self.client = cls_api_client
        self.skip_verification = skip_verification

    def get_ovf_files_map(self, ovf_location):
        """Get OVF template file paths to be used during uploads
        Note: This method returns OVF template paths for the template included
              in the SDK resources directory
        """

        ovf_files_map = {}
        ovf_dir = os.path.abspath(
            os.path.join(os.path.dirname(os.path.realpath(__file__)),
                         ovf_location))
        for file_name in os.listdir(ovf_dir):
            if file_name.endswith('.ovf') or file_name.endswith('.vmdk'):
                ovf_files_map[file_name] = os.path.join(ovf_dir, file_name)
        return ovf_files_map

    def create_local_library(self, storage_backings, lib_name):
        """:param storage_backings: Storage for the library
        :param lib_name: Name of the library
        :return: id of the created library
        """
        create_spec = vsphere_adaptor.get_content_client().LibraryModel()
        create_spec.name = lib_name
        create_spec.description = 'Local library backed by VC datastore'
        create_spec.type = vsphere_adaptor.get_content_client(
        ).LibraryModel.LibraryType.LOCAL
        create_spec.storage_backings = storage_backings

        # Create a local content library backed the VC datastore
        library_id = self.client.local_library_service.create(
            create_spec=create_spec, client_token=generate_random_uuid())
        logger.info('Local library created, ID: {0}'.format(library_id))

        return library_id

    def create_iso_library_item(self, library_id, iso_item_name, iso_filename):
        """:param library_id: item will be created on this library
        :param iso_item_name: name of the iso item to be created
        :param iso_filename: name of the iso file to be uploaded
        :return: id of the item created
        """
        # Create a new library item in the content library for uploading the
        # files
        library_item_id = self.create_library_item(
            library_id=library_id,
            item_name=iso_item_name,
            item_description='Sample iso file',
            item_type='iso',
        )
        assert library_item_id is not None
        logger.info('Library item created id: {0}'.format(library_item_id))

        # Upload an iso file to above library item, use the filename as the
        # item_filename
        iso_files_map = self.get_iso_file_map(item_filename=iso_filename,
                                              disk_filename=iso_filename)
        self.upload_files(library_item_id=library_item_id,
                          files_map=iso_files_map)
        logger.info(
            'Uploaded iso file to library item {0}'.format(library_item_id))
        return library_item_id

    def get_iso_file_map(self, item_filename, disk_filename):
        iso_files_map = {}
        iso_file_path = os.path.abspath(
            os.path.join(
                os.path.dirname(os.path.realpath(__file__)),
                self.ISO_FILE_RELATIVE_DIR + disk_filename,
            ))
        iso_files_map[item_filename] = iso_file_path
        return iso_files_map

    def get_ova_file_map(self, relative_path, local_filename):
        """Get OVA file paths to be used during uploads.

        :param relative_path: directory path under contentlibrary/resources
         for ova file
        :param local_filename: name of the file on local disk under resources
         directory
        :return: mapping of item's filename to full path for the file on local
         disk
        """
        ova_file_map = {}
        item_filename = os.path.basename(local_filename)
        ova_file_path = os.path.abspath(
            os.path.join(
                os.path.dirname(os.path.realpath(__file__)),
                relative_path,
                local_filename,
            ))
        ova_file_map[item_filename] = ova_file_path
        return ova_file_map

    def get_libraryitem_spec(
            self,
            client_token,  # pylint: disable=unused-argument
            name,
            description,
            library_id,  # pylint: disable=unused-argument
            library_item_type):
        """        Create library item spec

        """
        lib_item_spec = vsphere_adaptor.get_library_client().ItemModel()
        lib_item_spec.name = name
        lib_item_spec.description = description
        lib_item_spec.library_id = library_id
        lib_item_spec.type = library_item_type
        return lib_item_spec

    def create_library_item(self, library_id, item_name, item_description,
                            item_type):
        """Create a library item in the specified library
        """
        lib_item_spec = self.get_libraryitem_spec(
            client_token=generate_random_uuid(),
            name=item_name,
            description=item_description,
            library_id=library_id,
            library_item_type=item_type,
        )
        # Create a library item
        return self.client.library_item_service.create(
            create_spec=lib_item_spec, client_token=generate_random_uuid())

    def upload_files(self, library_item_id, files_map):
        """Upload a VM template to the published CL
        """
        # Create a new upload session for uploading the files
        session_id = self.client.upload_service.create(
            create_spec=vsphere_adaptor.get_item_client().UpdateSessionModel(
                library_item_id=library_item_id),
            client_token=generate_random_uuid(),
        )
        self.upload_files_in_session(files_map, session_id)
        self.client.upload_service.complete(session_id)
        self.client.upload_service.delete(session_id)

    def upload_files_in_session(self, files_map, session_id):
        for f_name, f_path in files_map.items():
            file_spec = self.client.upload_file_service.AddSpec(
                name=f_name,
                source_type=vsphere_adaptor.get_updatesession_client().File.
                SourceType.PUSH,
                size=os.path.getsize(f_path),
            )
            file_info = self.client.upload_file_service.add(
                session_id, file_spec)
            # Upload the file content to the file upload URL
            with open(f_path, 'rb') as local_file:
                request = urllib2.Request(file_info.upload_endpoint.uri,
                                          local_file)
                request.add_header('Cache-Control', 'no-cache')
                request.add_header('Content-Length',
                                   '{0}'.format(os.path.getsize(f_path)))
                request.add_header('Content-Type', 'text/ovf')
                if self.skip_verification and hasattr(
                        ssl, '_create_unverified_context'):
                    # Python 2.7.9 has stronger SSL certificate validation,
                    # so we need to pass in a context when dealing with
                    # self-signed certificates.
                    context = ssl._create_unverified_context()  # pylint: disable=protected-access
                    urllib2.urlopen(request, context=context)
                else:
                    # Don't pass context parameter since versions of Python
                    # before 2.7.9 don't support it.
                    urllib2.urlopen(request)

    def download_files(self, library_item_id, directory):
        """Download files from a library item

        Args:
            library_item_id: id for the library item to download files from
            directory: location on the client machine to download the files into

        """
        downloaded_files_map = {}
        # create a new download session for downloading the session files
        session_id = self.client.download_service.create(
            create_spec=vsphere_adaptor.get_item_client().DownloadSessionModel(
                library_item_id=library_item_id),
            client_token=generate_random_uuid(),
        )
        file_infos = self.client.download_file_service.list(session_id)
        for file_info in file_infos:
            self.client.download_file_service.prepare(session_id,
                                                      file_info.name)
            download_info = self.wait_for_prepare(session_id, file_info.name)
            if self.skip_verification and hasattr(ssl,
                                                  '_create_unverified_context'):
                # Python 2.7.9 has stronger SSL certificate validation,
                # so we need to pass in a context when dealing with self-signed
                # certificates.
                context = ssl._create_unverified_context()  # pylint: disable=protected-access
                response = urllib2.urlopen(  # pylint: disable=consider-using-with
                    url=download_info.download_endpoint.uri,
                    context=context)
            else:
                # Don't pass context parameter since versions of Python
                # before 2.7.9 don't support it.
                response = urllib2.urlopen(download_info.download_endpoint.uri)  # pylint: disable=consider-using-with
            file_path = os.path.join(directory, file_info.name)
            with open(file_path, 'wb') as local_file:
                local_file.write(response.read())
            downloaded_files_map[file_info.name] = file_path
        self.client.download_service.delete(session_id)
        return downloaded_files_map

    def wait_for_prepare(
        self,
        session_id,
        file_name,
        status_list=None,
        timeout=30,
        sleep_interval=1,
    ):
        """        Waits for a file to reach a status in the status list
         (default: prepared)
        This method will either timeout or return the result of
        downloadSessionFile.get(session_id, file_name)

        """
        if status_list is None:
            # Set default status list to prepared
            status_list = [
                vsphere_adaptor.get_updatesession_client().File.Status.PREPARED
            ]
        start_time = time.time()
        while (time.time() - start_time) < timeout:
            file_info = self.client.download_file_service.get(
                session_id, file_name)
            if file_info.status in status_list:
                return file_info
            else:
                time.sleep(sleep_interval)
        raise Exception(
            'timed out after waiting {0} seconds for file {1} to reach'
            ' a terminal state'.format(timeout, file_name))

    def get_item_id_by_name(self, name):
        """        Returns the identifier of the item with the given name.

        Args:
            name (str): The name of item to look for

        Returns:
            str: The item ID or None if the item is not found
        """
        find_spec = vsphere_adaptor.get_library_client().Item.FindSpec(
            name=name)
        item_ids = self.client.library_item_service.find(find_spec)
        item_id = item_ids[0] if item_ids else None
        if item_id:
            logger.info('Library item ID: {0}'.format(item_id))
        else:
            logger.info('Library item with name \'{0}\' not found'.format(name))
        return item_id

    def deploy_ovf_template(self, vm_name, lib_item_id, ovf_summary,
                            deployment_target, storage_profile_id):
        # Build the deployment spec
        deployment_spec = vsphere_adaptor.get_ovf_client(
        ).LibraryItem.ResourcePoolDeploymentSpec(
            name=vm_name,
            annotation=ovf_summary.annotation,
            accept_all_eula=True,
            network_mappings=None,
            storage_mappings=None,
            storage_provisioning=None,
            storage_profile_id=storage_profile_id,
            locale=None,
            flags=None,
            additional_parameters=None,
        )

        # Deploy the ovf template
        result = self.client.ovf_lib_item_service.deploy(
            lib_item_id,
            deployment_target,
            deployment_spec,
            client_token=generate_random_uuid(),
        )

        # The type and ID of the target deployment is available in the
        # deployment result.
        if result.succeeded:
            logger.info(
                'Deployment successful. Result resource: {0}, ID: {1}'.format(
                    result.resource_id.type, result.resource_id.id))
            self.vm_id = result.resource_id.id
            error = result.error
            if error is not None:
                for warning in error.warnings:
                    logger.info('OVF warning: {}'.format(warning.message))

            # Power on the VM and wait  for the power on operation to be
            # completed
            self.vm_obj = get_obj_by_mo_id(
                self.client.service_manager.content,
                [vsphere_adaptor.get_vim().VirtualMachine], self.vm_id)
            assert self.vm_obj is not None
            return self.vm_obj
        else:
            logger.info('Deployment failed.')
            for error in result.error.errors:
                logger.info('OVF error: {}'.format(error.message))
