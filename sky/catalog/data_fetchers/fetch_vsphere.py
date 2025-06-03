""" data fetcher for vsphere """
import json
import logging
import os
import typing

from sky.adaptors import common as adaptors_common
from sky.adaptors import vsphere as vsphere_adaptor
from sky.catalog.common import get_catalog_path
from sky.provision.vsphere.common.cls_api_client import ClsApiClient

if typing.TYPE_CHECKING:
    import pandas as pd
else:
    pd = adaptors_common.LazyImport('pandas')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# TODO: Add more GPUs and a GPU full/short name mapping
PRESET_GPU_LIST = [
    {
        'Model': 'V100',
        'Type': 'GPU',
        'MemoryMB': 1024 * 16,
        'vCPUs': 8,
        'fullNames': [
            'Tesla V100',
            'Tesla V100-PCIE-16GB',
            'Tesla V100-PCIE-32GB',
            'Tesla V100-SXM2-16GB',
        ],
    },
    {
        'Model': 'P100',
        'Type': 'GPU',
        'MemoryMB': 1024 * 16,
        # Estimated
        'vCPUs': 8,
        'fullNames': [
            'Tesla P100-PCIE-16GB',
            'Tesla P100-PCIE-12GB',
            'Tesla P100-SXM2-16GB',
            'Tesla P100-SXM2-12GB',
        ],
    },
    {
        'Model': 'T4',
        'Type': 'GPU',
        'MemoryMB': 1024 * 16,
        'vCPUs': 4,
        'fullNames': ['Tesla T4'],
    },
    {
        'Model': 'P4',
        'Type': 'GPU',
        'MemoryMB': 1024 * 8,
        # Estimated
        'vCPUs': 8,
        'fullNames': ['Tesla P4'],
    },
    {
        'Model': 'K80',
        'Type': 'GPU',
        'MemoryMB': 1024 * 24,
        'vCPUs': 8,
        'fullNames': ['Tesla K80'],
    },
    {
        'Model': 'A100',
        'Type': 'GPU',
        'MemoryMB': 1024 * 40,
        'vCPUs': 8,
        'fullNames': ['Tesla A100-PCIE-40GB', 'Tesla A100-SXM4-40GB'],
    },
    {
        'Model': '1080',
        'Type': 'GPU',
        'MemoryMB': 1024 * 8,
        # Estimated
        'vCPUs': 4,
        'fullNames': ['GeForce GTX 1080 Ti', 'GeForce GTX 1080'],
    },
    {
        'Model': '2080',
        'Type': 'GPU',
        'MemoryMB': 1024 * 8,
        # Estimated
        'vCPUs': 4,
        'fullNames': [
            'GeForce RTX 2080 Ti',
            'GeForce RTX 2080 SUPER',
            'GeForce RTX 2080',
        ],
    },
    {
        'Model': 'A5000',
        'Type': 'GPU',
        'MemoryMB': 1024 * 24,
        # Estimated
        'vCPUs': 8,
        'fullNames': ['NVIDIA RTX A5000'],
    },
    {
        'Model': 'A6000',
        'Type': 'GPU',
        'MemoryMB': 1024 * 48,
        # Estimated
        'vCPUs': 8,
        'fullNames': ['NVIDIA RTX A6000'],
    },
    {
        'Model': 'K1200',
        'Type': 'GPU',
        'MemoryMB': 1024 * 4,
        # Estimated
        'vCPUs': 2,
        'fullNames': ['Quadro K1200'],
    },
    {
        'Model': 'M40',
        'Type': 'GPU',
        'MemoryMB': 1024 * 24,
        # Estimated
        'vCPUs': 8,
        'fullNames': ['Quadro M40'],
    },
    {
        'Model': 'M60',
        'Type': 'GPU',
        'MemoryMB': 1024 * 8,
        # Estimated
        'vCPUs': 4,
        'fullNames': ['Quadro M60'],
    },
    {
        'Model': 'M5000',
        'Type': 'GPU',
        'MemoryMB': 1024 * 8,
        # Estimated
        'vCPUs': 4,
        'fullNames': ['Quadro M5000'],
    },
    {
        'Model': 'M4000',
        'Type': 'GPU',
        'MemoryMB': 1024 * 8,
        # Estimated
        'vCPUs': 4,
        'fullNames': ['Quadro M4000'],
    },
    {
        'Model': 'Titan X',
        'Type': 'GPU',
        'MemoryMB': 1024 * 12,
        # Estimated
        'vCPUs': 8,
        'fullNames': ['GeForce GTX TITAN X', 'GeForce GTX TITAN X (Pascal)'],
    },
    {
        'Model': 'Titan V',
        'Type': 'GPU',
        'MemoryMB': 1024 * 12,
        # Estimated
        'vCPUs': 8,
        'fullNames': ['TITAN V'],
    },
    {
        'Model': 'P5000',
        'Type': 'GPU',
        'MemoryMB': 1024 * 16,
        # Estimated
        'vCPUs': 8,
        'fullNames': ['Quadro P5000'],
    },
    {
        'Model': 'P6000',
        'Type': 'GPU',
        'MemoryMB': 1024 * 24,
        # Estimated
        'vCPUs': 8,
        'fullNames': ['Quadro P6000'],
    },
    {
        'Model': 'RTX 6000',
        'Type': 'GPU',
        'MemoryMB': 1024 * 24,
        # Estimated
        'vCPUs': 8,
        'fullNames': ['NVIDIA RTX 6000'],
    },
    {
        'Model': '2080 Ti',
        'Type': 'GPU',
        'MemoryMB': 1024 * 11,
        # Estimated
        'vCPUs': 4,
        'fullNames': ['GeForce RTX 2080 Ti'],
    },
    {
        'Model': '3070',
        'Type': 'GPU',
        'MemoryMB': 1024 * 8,
        # Estimated
        'vCPUs': 4,
        'fullNames': ['GeForce RTX 3070'],
    },
    {
        'Model': '3080',
        'Type': 'GPU',
        'MemoryMB': 1024 * 10,
        # Estimated
        'vCPUs': 4,
        'fullNames': ['GeForce RTX 3080'],
    },
    {
        'Model': '3090',
        'Type': 'GPU',
        'MemoryMB': 1024 * 24,
        # Estimated
        'vCPUs': 8,
        'fullNames': ['GeForce RTX 3090'],
    },
    {
        'Model': '4090',
        'Type': 'GPU',
        'MemoryMB': 1024 * 24,
        # Estimated
        'vCPUs': 8,
        'fullNames': ['GeForce RTX 4090'],
    },
    {
        'Model': '4080',
        'Type': 'GPU',
        'MemoryMB': 1024 * 16,
        # Estimated
        'vCPUs': 8,
        'fullNames': ['GeForce RTX 4080'],
    },
    {
        'Model': '4070',
        'Type': 'GPU',
        'MemoryMB': 1024 * 12,
        # Estimated
        'vCPUs': 8,
        'fullNames': ['GeForce RTX 4070'],
    },
    {
        'Model': 'G200eW3',
        'Type': 'GPU',
        'MemoryMB': 1024 * 12,
        'vCPUs': 8,
        'fullNames': ['Integrated Matrox G200eW3 Graphics Controller'],
    },
    {
        'Model': 'K1200',
        'Type': 'GPU',
        'MemoryMB': 1024 * 4,
        'vCPUs': 2,
        'fullNames': ['GM107GL [Quadro K1200]'],
    },
]
ACCELERATORS_CSV_PATH = get_catalog_path('vsphere/accelerators.csv')
CPU_INSTANCE_TYPES = [
    {
        'InstanceType': 'cpu.xsmall',
        'vCPUs': 2,
        'MemoryMB': 1024 * 2,
    },
    {
        'InstanceType': 'cpu.small',
        'vCPUs': 2,
        'MemoryMB': 1024 * 4,
    },
    {
        'InstanceType': 'cpu.medium',
        'vCPUs': 2,
        'MemoryMB': 1024 * 8,
    },
    {
        'InstanceType': 'cpu.large',
        'vCPUs': 4,
        'MemoryMB': 1024 * 16,
    },
    {
        'InstanceType': 'cpu.xlarge',
        'vCPUs': 4,
        'MemoryMB': 1024 * 32,
    },
    {
        'InstanceType': 'cpu.2xlarge',
        'vCPUs': 8,
        'MemoryMB': 1024 * 64,
    },
    {
        'InstanceType': 'cpu.4xlarge',
        'vCPUs': 16,
        'MemoryMB': 1024 * 128,
    },
    {
        'InstanceType': 'cpu.8xlarge',
        'vCPUs': 32,
        'MemoryMB': 1024 * 128,
    },
]
GPU_MANUFACTURERS = ['nvidia', 'amd', 'intel']


def initialize_accelerators_csv():
    if not os.path.exists(ACCELERATORS_CSV_PATH):
        try:
            # Create an empty DataFrame with the desired columns
            data = [{
                'Model': gpu.get('Model'),
                'Type': gpu.get('Type'),
                'MemoryMB': gpu.get('MemoryMB'),
                'vCPUs': gpu.get('vCPUs'),
                'fullNames': gpu.get('fullNames'),
            } for gpu in PRESET_GPU_LIST]
            df = pd.DataFrame(data)

            # Write DataFrame to CSV
            df.to_csv(ACCELERATORS_CSV_PATH, index=False)
            return
        except Exception as e:
            logger.error(f'Failed to initialize accelerators.csv: {e}')
            raise e


def get_accelerators_from_csv() -> typing.List[typing.Dict]:
    try:
        df = pd.read_csv(ACCELERATORS_CSV_PATH)
        return df.to_dict('records')
    except Exception as e:
        logger.error(f'Failed to get accelerators from accelerators.csv: {e}')
        raise e


def initialize_hosts_csv(csv_saving_path: str,
                         hosts_for_csv: typing.List[typing.Dict]):
    try:
        with open(csv_saving_path, 'a', encoding='utf-8') as f:
            for host in hosts_for_csv:
                f.write(f'{host.get("HostName")},{host.get("MoRefID")},'
                        f'{host.get("vCenter")},{host.get("Datacenter")},'
                        f'{host.get("Cluster")},{host.get("TotalCPUMHz")},'
                        f'{host.get("AvailableCPUCapacityMHz")},'
                        f'{host.get("TotalMemoryMB")},'
                        f'{host.get("AvailableMemoryMB")},'
                        f'"{host.get("Accelerators")}",'
                        f'{host.get("CPUMHz")},{host.get("UUID")}\n')
                return
    except Exception as e:
        logger.error(f'Failed to initialize hosts.csv: {e}')
        raise e


def summarize_by_cluster_and_gpu(
    hosts_to_summarize: typing.List[typing.Dict],
) -> typing.Dict[str, typing.Any]:
    try:
        gpu_cluster_dict: typing.Dict[str, typing.Dict[str, typing.Any]] = {}
        gpu_list = get_accelerators_from_csv()
        weakest_host_of_each_cluster = {}
        for host in hosts_to_summarize:
            # If cluster is not in gpu_cluster_dict, initialize cluster entry
            cluster_name = host.get('Cluster', '')

            #  Deal with host with gpus
            if host.get('Accelerators') != []:
                for accelerator in host.get('Accelerators', []):
                    gpu_key = accelerator.get('DeviceName', '')
                    # if accelerator.get('Status') == 'Available':
                    # we just list all the instances we supported there
                    # no matter whether
                    # the GPU is available or not.
                    if cluster_name not in gpu_cluster_dict:
                        gpu_cluster_dict[cluster_name] = {}
                    if gpu_key not in gpu_cluster_dict[cluster_name]:
                        # Go through PRESET_GPU_LIST to find the GPU's
                        # memory and vCPU
                        # TODO: Confirm if 1:1 mapping between VM memory and
                        #  GPU memory is good.
                        for gpu in gpu_list:
                            model = gpu.get('Model')
                            if model and isinstance(model, str):
                                if model.lower() in gpu_key.lower():
                                    gpu_cluster_dict[cluster_name][gpu_key] = {
                                        'AcceleratorCount': 1,
                                        'vCPUs': gpu.get('vCPUs', 0),
                                        'MemoryMB': gpu.get('MemoryMB', 0),
                                        'Accelerators': accelerator,
                                        'GpuInfo': host.get('GpuInfo', {}),
                                    }
                                    break
            # Try to compare its capacity with the weakest CPU host of
            # the cluster
            if cluster_name not in weakest_host_of_each_cluster:
                weakest_host_of_each_cluster[cluster_name] = host.get(
                    'TotalCPUcores', 0)
            else:
                if (host.get('TotalCPUcores') and host.get('TotalCPUcores') <
                        weakest_host_of_each_cluster[cluster_name]):
                    weakest_host_of_each_cluster[cluster_name] = host.get(
                        'TotalCPUcores', 0)
        return {
            'gpu_cluster_dict': gpu_cluster_dict,
            'weakest_host_of_each_cluster': weakest_host_of_each_cluster,
        }
    except Exception as e:
        logger.error(f'Failed to summarize hosts: {e}')
        raise e


def initialize_vms_csv(csv_saving_path: str,
                       hosts_for_csv: typing.List[typing.Dict],
                       vcenter_name: str) -> None:
    try:
        with open(csv_saving_path, 'a', encoding='utf-8') as f:
            # Read info from summarize_by_cluster_and_gpu(hosts)
            summary_result = summarize_by_cluster_and_gpu(hosts_for_csv)
            gpu_clusters_dict = summary_result.get('gpu_cluster_dict')
            if gpu_clusters_dict:
                for cluster_name in gpu_clusters_dict:
                    gpus_dict = gpu_clusters_dict[cluster_name]
                    if gpus_dict:
                        for gpu_name in gpus_dict:
                            gpu_short_name = gpu_name
                            for gpu in PRESET_GPU_LIST:
                                model = gpu.get('Model')
                                if model and isinstance(model, str):
                                    if model.lower() in gpu_name.lower():
                                        gpu_short_name = gpu.get('Model')
                            if gpu_short_name == gpu_name:
                                logger.warning(
                                    f'Failed to find GPU {gpu_name}\'s '
                                    f'short name in the preset GPU list. '
                                    f'Use its full name instead.')
                            gpu_info = gpus_dict[gpu_name]
                            # TODO: add support for instance name with
                            #  gpu count, like gpu.A100.4
                            accelerator_count = gpu_info.get(
                                'AcceleratorCount', 0)
                            vcpus = gpu_info.get('vCPUs', 0)
                            memory_gb = round(
                                gpu_info.get('MemoryMB') / 1024, 2)
                            gpu_info_str = gpu_info['GpuInfo']
                            f.write(f'gpu.{gpu_short_name},{gpu_short_name},'
                                    f'{accelerator_count},'
                                    f'{vcpus},'
                                    f'{memory_gb},'
                                    f'\"{gpu_info_str}\",0.0,0.0,'
                                    f'{vcenter_name},{cluster_name}\n')
            cpu_host_dict = summary_result.get('weakest_host_of_each_cluster')
            if cpu_host_dict:
                for cluster_name in cpu_host_dict:
                    minimal_cpu_cores = cpu_host_dict[cluster_name]
                    # find the instance type that match the host's capacity
                    for instance_type in CPU_INSTANCE_TYPES:
                        if instance_type.get('vCPUs') <= minimal_cpu_cores:
                            vcpus = instance_type.get('vCPUs')
                            memory_mb = (instance_type.get('MemoryMB', 0))
                            # Make sure that memoryMB is a number before
                            # proceeding
                            if isinstance(memory_mb, (int, float)):
                                memory_in_gb = round(memory_mb / 1024, 2)
                            else:
                                raise TypeError(
                                    f'The value for \'MemoryMB\' is not a '
                                    f'number: {memory_mb}')
                            instance_type_str = instance_type.get(
                                'InstanceType')
                            f.write(f'{instance_type_str},,,'
                                    f'{vcpus},{memory_in_gb},,0.0,0.0,'
                                    f'{vcenter_name},{cluster_name}\n')
        return
    except Exception as e:
        logger.error(f'Failed to initialize vms.csv: {e}')
        raise e


def initialize_images_csv(csv_saving_path: str, vc_object,
                          vcenter_name: str) -> None:
    vc_servicemanager = vc_object.servicemanager
    vc_stub_config = vc_servicemanager.stub_config
    vc_tag_obj = vsphere_adaptor.get_tagging_client().Tag(vc_stub_config)
    vc_tag_association_obj = vsphere_adaptor.get_tagging_client(
    ).TagAssociation(vc_stub_config)
    cl_client_obj = ClsApiClient(vc_servicemanager)
    content_library_ids = cl_client_obj.local_library_service.list()
    library_item_service_item_obj = cl_client_obj.library_item_service
    vmtx_service_item_obj = cl_client_obj.vmtx_service
    with open(csv_saving_path, 'a', encoding='utf-8') as f:
        for lib_id in content_library_ids:
            library_obj = cl_client_obj.local_library_service.get(
                library_id=lib_id)
            library_item_ids = library_item_service_item_obj.list(
                library_id=library_obj.id)
            for item_id in library_item_ids:
                item = library_item_service_item_obj.get(
                    library_item_id=item_id)
                if item.type == 'vm-template':
                    item_spec = vmtx_service_item_obj.get(
                        template_library_item=item_id)
                    item_cpu = item_spec.cpu.count
                    item_memory = item_spec.memory.size_mib
                elif item.type in ['ovf', 'ova']:
                    item_cpu = 2
                    item_memory = 2048
                else:
                    continue
                dynamic_id = vsphere_adaptor.get_std_client().DynamicID(
                    type='com.vmware.content.library.Item', id=item.id)
                # Retrieve the tag_ids associated with this template item
                associated_tags = vc_tag_association_obj.list_attached_tags(
                    object_id=dynamic_id)
                # For each tag_id, get the tag details
                gpu_tags = []
                # Noted that if an image is tagged with more than one CPU tags,
                # it will be listed multiple times in
                # images.csv, so I added this logic.
                cpu_image_added = False
                for tag_id in associated_tags:
                    tag = vc_tag_obj.get(tag_id)
                    tag_name = tag.name
                    if 'SKYPILOT-CPU' in tag_name.upper():
                        if not cpu_image_added:
                            f.write(f'{item.id},{vcenter_name},{item_cpu},'
                                    f'{item_memory},,,\'[]\'\n')
                            cpu_image_added = True
                    elif 'GPU-' in tag_name.upper() or (
                            'SKYPILOT-' in tag_name.upper() and
                            'SKYPILOT-CPU' not in tag_name.upper()):
                        gpu_name = tag_name.split('-')[1]
                        if gpu_name not in gpu_tags:
                            gpu_tags.append(gpu_name)
                if gpu_tags:
                    gpu_tags_str = str(gpu_tags).replace('\'', '\"')
                    f.write(f'{item.id},{vcenter_name},{item_cpu},{item_memory}'
                            f',,,\'{gpu_tags_str}\'\n')


def initialize_instance_image_mapping_csv(vms_csv_path: str,
                                          images_csv_path: str,
                                          csv_saving_path: str):
    # Read vms.csv and images.csv
    vms_df = pd.read_csv(vms_csv_path, encoding='utf-8')
    images_df = pd.read_csv(images_csv_path, encoding='utf-8')

    # Assuming GpuTags are stored as string lists
    images_df['GpuTags'] = images_df['GpuTags'].apply(
        lambda x: json.loads(x[1:-1].lower()))

    # Merging on vCenter
    merged_df = pd.merge(vms_df,
                         images_df,
                         left_on='Region',
                         right_on='vCenter')
    results = []

    for _, row in merged_df.iterrows():
        if pd.isna(row['AcceleratorName']) and row['GpuTags'] == []:
            results.append(
                [row['InstanceType'], row['ImageID'], row['vCenter']])
        else:
            if not pd.isna(row['AcceleratorName']):
                vm_gpu_fullname = row['AcceleratorName'].lower()
                vm_gpu_manufacturer_fullname = json.loads(
                    row['GpuInfo'].replace(
                        '\'', '\"'))['Gpus'][0]['Manufacturer'].lower()
                mapping_found_by_gpu_name = False
                default_manufacturer_name = None
                for image_gpu_tag in row['GpuTags']:
                    image_gpu_tag_lower = image_gpu_tag.lower()
                    if image_gpu_tag_lower in vm_gpu_fullname:
                        results.append([
                            row['InstanceType'], row['ImageID'], row['vCenter']
                        ])
                        mapping_found_by_gpu_name = True
                        break
                    elif image_gpu_tag_lower in vm_gpu_manufacturer_fullname:
                        default_manufacturer_name = image_gpu_tag_lower
                if (not mapping_found_by_gpu_name and
                        default_manufacturer_name is not None):
                    results.append(
                        [row['InstanceType'], row['ImageID'], row['vCenter']])
            else:
                for manufacturer in GPU_MANUFACTURERS:
                    if manufacturer in row['GpuTags']:
                        results.append([
                            row['InstanceType'], row['ImageID'], row['vCenter']
                        ])

    result_df = pd.DataFrame(results,
                             columns=['InstanceType', 'ImageID', 'vCenter'])
    result_df.drop_duplicates(inplace=True)
    result_df.to_csv(csv_saving_path,
                     mode='a',
                     header=False,
                     index=False,
                     encoding='utf-8')
