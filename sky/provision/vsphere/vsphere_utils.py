"""vsphere utils
"""
import http.cookies as http_cookies
import os
import ssl
import typing
from typing import Any, Dict, List, Optional

from sky import exceptions
from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.adaptors import vsphere as vsphere_adaptor
from sky.clouds.service_catalog import vsphere_catalog
from sky.clouds.service_catalog.common import get_catalog_path
from sky.clouds.service_catalog.data_fetchers.fetch_vsphere import (
    initialize_accelerators_csv)
from sky.clouds.service_catalog.data_fetchers.fetch_vsphere import (
    initialize_hosts_csv)
from sky.clouds.service_catalog.data_fetchers.fetch_vsphere import (
    initialize_images_csv)
from sky.clouds.service_catalog.data_fetchers.fetch_vsphere import (
    initialize_instance_image_mapping_csv)
from sky.clouds.service_catalog.data_fetchers.fetch_vsphere import (
    initialize_vms_csv)
from sky.provision.vsphere.common import vim_utils
from sky.provision.vsphere.common.cls_api_client import ClsApiClient
from sky.provision.vsphere.common.cls_api_helper import ClsApiHelper
from sky.provision.vsphere.common.id_generator import generate_random_uuid
from sky.provision.vsphere.common.service_manager_factory import (
    ServiceManagerFactory)
from sky.provision.vsphere.common.vim_utils import create_spec_with_script
from sky.provision.vsphere.common.vim_utils import poweron_vm
from sky.provision.vsphere.common.vim_utils import wait_for_tasks
from sky.provision.vsphere.common.vim_utils import wait_internal_ip_ready

if typing.TYPE_CHECKING:
    import yaml
else:
    yaml = adaptors_common.LazyImport('yaml')

logger = sky_logging.init_logger(__name__)

CREDENTIALS_PATH = '~/.vsphere/credential.yaml'
VM_PREFIX = 'skypilot-vm-'


class VsphereError(Exception):
    pass


class VsphereClient:
    """VsphereClient
    """

    def __init__(
        self,
        server: str,
        username: str,
        password: str,
        clusters: List[Dict[str, str]],
        skip_verification: bool,
    ) -> None:
        self.__server = server
        self.__username = username
        self.__password = password
        self.clusters = clusters
        self.skip_verification = skip_verification
        self.servicemanager = None
        self.tag = None
        self.tag_association = None

    def connect(self):
        if not self.servicemanager:
            self.servicemanager = ServiceManagerFactory.get_service_manager(
                self.__server, self.__username, self.__password,
                self.skip_verification)

    def disconnect(self):
        if self.servicemanager:
            self.servicemanager.disconnect()

    def remove_instances(self, *instance_ids: str) -> Dict[str, Any]:  # pylint: disable=unused-argument
        """Terminate instances."""
        ### TODO: Add implementation
        # pass
        return {}

    def list_instances(self):
        ### TODO: Add implementation
        return []

    def filter_instances(
        self,
        instance_uuids: Optional[List[str]] = None,
        filters: Optional[List[Dict[str, str]]] = None,
        vsphere_cluster_names: Optional[List[str]] = None,
    ):
        """Filter the vm instances using custom attribute
        """
        instances: List[Any] = []
        service_manager = self.servicemanager
        # If no instance_ids passed in, then get all VMs in vsphere cluster;
        # Else get instances by instance_ids.
        if service_manager:
            if not instance_uuids:
                instances = vim_utils.filter_vms_by_cluster_name(
                    service_manager.content, vsphere_cluster_names)
            else:
                for inst_id in instance_uuids:
                    instance_obj = (
                        service_manager.content.searchIndex.FindByUuid(
                            None, inst_id, True, True))
                    if instance_obj:
                        instances.append(instance_obj)

            if not filters:
                return instances

            filtered_result = []
            filter_keys = [f['Key'] for f in filters]
            for inst in instances:
                #  If the param: instance_ids is empty or none, the
                #  [startswith(VM_PREFIX)] condition
                #  will quickly filter out instances that are not part
                #  of skypilot
                if inst.name.startswith(VM_PREFIX):
                    # Filter the instances using Custom Attributes
                    cust_attributes = [(f.name, v.value)
                                       for f in inst.availableField
                                       if f.name in filter_keys
                                       for v in inst.customValue
                                       if f.key == v.key]
                    for filter_item in filters:
                        if (filter_item['Key'],
                                filter_item['Value']) not in cust_attributes:
                            break
                    else:
                        filtered_result.append(inst)

            return filtered_result
        else:
            raise VsphereError('Failed to connect to vSphere.')

    def check_credential(self):
        self.connect()

    def create_instances(
        self,
        cluster: str,
        host_mobid: str,
        lib_item_id: str,
        spec,
        customization_spec_str: str,
    ):
        # Set up
        self.vm_name = VM_PREFIX + str(generate_random_uuid())
        self.connect()
        self.client = ClsApiClient(self.servicemanager)
        self.helper = ClsApiHelper(self.client, self.skip_verification)
        service_manager = self.servicemanager
        if service_manager:
            # Find the cluster's resource pool moid
            cluster_obj = vim_utils.get_objs_by_names(
                service_manager.content,
                [vsphere_adaptor.get_vim().ClusterComputeResource], [cluster])
            if not cluster_obj.get(cluster):
                logger.error(f'Failed to find cluster {cluster}')
                raise exceptions.ResourcesUnavailableError(
                    f'Failed to find cluster {cluster}')
            cluster_obj = cluster_obj[cluster]
            logger.info('Cluster Moref: {0}'.format(cluster_obj))

            # Find the deployment target
            deployment_target = vsphere_adaptor.get_ovf_client(
            ).LibraryItem.DeploymentTarget(
                resource_pool_id=cluster_obj.resourcePool._GetMoId(),  # pylint: disable=protected-access
                host_id=host_mobid)

            ovf_summary = self.client.ovf_lib_item_service.filter(
                ovf_library_item_id=lib_item_id, target=deployment_target)
            logger.info('Found an OVF template :{0} to deploy.'.format(
                ovf_summary.name))

            # Find out the storage profile id
            profile_id = self.get_skypilot_profile_id()
            if profile_id is None:
                logger.error('The VM storage policy \'skypilot_policy\''
                             ' is not available.')
                raise exceptions.ResourcesUnavailableError(
                    'The VM storage policy \'skypilot_policy\''
                    ' is not available.')
            # Deploy the ovf template
            vm_obj = self.helper.deploy_ovf_template(
                vm_name=self.vm_name,
                lib_item_id=lib_item_id,
                ovf_summary=ovf_summary,
                deployment_target=deployment_target,
                storage_profile_id=profile_id,
            )

            customization_spec = create_spec_with_script(
                vm_obj, customization_spec_str)
            check_customization_spec = vm_obj.CheckCustomizationSpec(
                spec=customization_spec)
            if check_customization_spec:
                logger.error(
                    f'Check customization spec failed for VM {vm_obj.name}: '
                    f'{check_customization_spec}')
                raise exceptions.ResourcesUnavailableError(
                    f'Check customization spec failed for VM {vm_obj.name}: '
                    f'{check_customization_spec}')
            wait_for_tasks(
                self.client.service_manager.content,
                [
                    vm_obj.Reconfigure(spec),
                    vm_obj.CustomizeVM_Task(spec=customization_spec),
                ],
            )
            poweron_vm(self.client.service_manager.content, vm_obj)
            wait_internal_ip_ready(vm_obj)
            return vm_obj.summary.config.instanceUuid
        else:
            raise VsphereError('Failed to connect to vSphere.')

    def set_tags(self, head_instance_uuid: str, tags: List[Dict[str, str]]):
        """Set tag for vm instance"""
        service_manager = self.servicemanager
        if service_manager:
            content = self.servicemanager.content
            cfm = content.customFieldsManager

            # Get the instance Object by instance_uuid
            vm = content.searchIndex.FindByUuid(None, head_instance_uuid, True,
                                                True)

            # Create the Field if not exsit, then set value.
            fields = [f.name for f in vm.availableField]
            for tag in tags:
                if tag['Key'] not in fields:
                    new_field = cfm.AddFieldDefinition(
                        tag['Key'],
                        vsphere_adaptor.get_vim().VirtualMachine)
                    cfm.SetField(vm, new_field.key, tag['Value'])
                    continue
                exi_key = [
                    fld.key
                    for fld in vm.availableField
                    if fld.name == tag['Key']
                ][0]
                cfm.SetField(vm, exi_key, tag['Value'])
        else:
            raise VsphereError('Failed to connect to vSphere.')

    def get_skypilot_profile_id(self):
        pm = self.get_pbm_manager()
        profile_ids = pm.PbmQueryProfile(
            resourceType=vsphere_adaptor.get_pbm().profile.ResourceType(
                resourceType='STORAGE'),
            profileCategory='REQUIREMENT',
        )

        # hard code here. should support configure later.
        profile_name = 'skypilot_policy'
        storage_profile_id = None
        if profile_ids:
            profiles = pm.PbmRetrieveContent(profileIds=profile_ids)
            for profile in profiles:
                if profile_name in profile.name:
                    storage_profile_id = profile.profileId.uniqueId
                    break
        return storage_profile_id

    def get_pbm_manager(self):
        self.connect()
        pbm_si, pm_content = self._create_pbm_connection(  # pylint: disable=unused-variable
            self.servicemanager.si._stub)  # pylint: disable=protected-access
        pm = pm_content.profileManager
        return pm

    # we should not call this method directly
    def _create_pbm_connection(self, vpxd_stub):
        session_cookie = vpxd_stub.cookie.split('\"')[1]
        http_context = vsphere_adaptor.get_vmomi_support().GetHttpContext()
        cookie = http_cookies.SimpleCookie()
        cookie['vmware_soap_session'] = session_cookie
        http_context['cookies'] = cookie
        vsphere_adaptor.get_vmomi_support().GetRequestContext(
        )['vcSessionCookie'] = session_cookie
        hostname = vpxd_stub.host.split(':')[0]

        context = None
        if hasattr(ssl, '_create_unverified_context'):
            context = ssl._create_unverified_context()  # pylint: disable=protected-access
        pbm_stub = vsphere_adaptor.get_pyvmomi().SoapStubAdapter(
            host=hostname,
            version='pbm.version.version1',
            path='/pbm/sdk',
            poolSize=0,
            sslContext=context,
        )
        pbm_si = vsphere_adaptor.get_pbm().ServiceInstance(
            'ServiceInstance', pbm_stub)
        pbm_content = pbm_si.RetrieveContent()

        return pbm_si, pbm_content


def get_vsphere_credentials(name=None):
    """The credential format is:
            vcenters:
              - name: vcenter1
                username: xxxx
                password: xxxx
                skip_verification: true
                clusters:
                - name: cluster1
                - name: cluster2
              - name: vcenter2
                username: xxxx
                password: xxxx
                skip_verification: true
                clusters:
                - name: cluster1
                - name: cluster2
    """
    credential_path = os.path.expanduser(CREDENTIALS_PATH)
    assert os.path.exists(
        credential_path), f'Missing credential file at {credential_path}.'
    with open(credential_path, 'r', encoding='utf-8') as file:
        credential = yaml.safe_load(file)
        vcenters = credential['vcenters']
        if name is None:
            return vcenters
        for vcenter in vcenters:
            if vcenter['name'] == name:
                return vcenter
        raise VsphereError(f' Failed to find vcenter which name is: {name}.')


def initialize_vsphere_data():
    os.makedirs(get_catalog_path('vsphere'), exist_ok=True)
    initialize_accelerators_csv()

    vms_csv_path = get_catalog_path('vsphere/vms.csv')
    with open(vms_csv_path, 'w', encoding='utf-8') as f:
        f.write(vsphere_catalog.VSPHERE_CATALOG_HEADER + '\n')
    images_csv_path = get_catalog_path('vsphere/images.csv')
    with open(images_csv_path, 'w', encoding='utf-8') as f:
        f.write('ImageID,vCenter,CPU,Memory,OS,OSVersion,GpuTags\n')
    hosts_csv_path = get_catalog_path('vsphere/hosts.csv')
    with open(hosts_csv_path, 'w', encoding='utf-8') as f:
        f.write(
            'HostName,MobID,vCenter,Datacenter,Cluster,TotalCPUs,AvailableCPUs,'
            'TotalMemory(MB),AvailableMemory(MB),GPU,cpuMhz,UUID\n')
    instance_image_mapping_csv_path = get_catalog_path(
        'vsphere/instance_image_mapping.csv')
    with open(instance_image_mapping_csv_path, 'w', encoding='utf-8') as f:
        f.write('InstanceType,ImageID,vCenter\n')
    skip_key = 'skip_verification'
    for vcenter in get_vsphere_credentials():
        if skip_key not in vcenter:
            vcenter[skip_key] = False
        vc_object = VsphereClient(
            vcenter['name'],
            vcenter['username'],
            vcenter['password'],
            vcenter['clusters'],
            vcenter['skip_verification'],
        )
        vcenter_name = vcenter['name']
        vc_object.connect()
        vc_servicemanager = vc_object.servicemanager
        vc_content = vc_servicemanager.content

        cluster_name_dicts = vc_object.clusters
        hosts = vim_utils.get_hosts_by_cluster_names(vc_content, vcenter_name,
                                                     cluster_name_dicts)
        initialize_hosts_csv(hosts_csv_path, hosts)
        initialize_vms_csv(vms_csv_path, hosts, vcenter_name)
        initialize_images_csv(images_csv_path, vc_object, vcenter_name)
        initialize_instance_image_mapping_csv(vms_csv_path, images_csv_path,
                                              instance_image_mapping_csv_path)
        vc_object.servicemanager.disconnect()
