'''
RunPod library wrapper, formats the input/output of the RunPod library for SkyPilot.
'''

import runpod


GPU_NAME_MAP = {
    "NVIDIA A100 80GB PCIe": "A100-80GB",
    "NVIDIA A100-PCIE-40GB": "A100-40GB",
    "NVIDIA A100-SXM4-80GB": "A100-80GB-SXM4",
    "NVIDIA A30": "A30",
    "NVIDIA A40": "A40",
    "NVIDIA GeForce RTX 3070": "RTX3070",
    "NVIDIA GeForce RTX 3080": "RTX3080",
    "NVIDIA GeForce RTX 3080 Ti": "RTX3080Ti",
    "NVIDIA GeForce RTX 3090": "RTX3090",
    "NVIDIA GeForce RTX 3090 Ti": "RTX3090Ti",
    "NVIDIA GeForce 4070 Ti": "RTX4070Ti",
    "NVIDIA GeForce RTX 4080": "RTX4080",
    "NVIDIA GeForce RTX 4090": "RTX4090",
    "NVIDIA H100 80GB HBM3": "H100-80GB-HBM3",
    "NVIDIA H100 PCIe": "H100-PCIe",
    "NVIDIA L40": "L40",
    "NVIDIA RTX 6000 Ada Generation": "RTX6000-Ada",
    "NVIDIA RTX A4000": "RTXA4000",
    "NVIDIA RTX A4500": "RTXA4500",
    "NVIDIA RTX A5000": "RTXA5000",
    "NVIDIA RTX A6000": "RTXA6000",
    "Quadro RTX 5000": "RTX5000",
    "Tesla V100-FHHL-16GB": "V100-16GB-FHHL",
    "V100-SXM2-16GB": "V100-16GB-SXM2",
}


def list_instances(api_key: str):
    '''
    Lists instances associated with API key.
    '''
    instances = runpod.get_pods()

    instance_list = {}
    for instance in instances:
        instance_list[instance['id']] = {}

        instance_list[instance['id']]['status'] = instance['desiredStatus']
        instance_list['name'] = instance['name']
        instance_list['ip'] = instance[]


def launch(name: str, instance_type: str, region: str, api_key: str, ssh_key_name: str):
    '''
    Launches an instance with the given parameters.

    Converts the instance_type to the RunPod GPU name, finds the specs for the GPU, and launches the instance.
    '''
    gpu_type = GPU_NAME_MAP[instance_type.split('_')[1]]
    gpu_quantity = int(instance_type.split('_')[0].replace('x', ''))

    gpu_specs = runpod.get_gpu(gpu_type)

    new_instance = runpod.create_pod(
        name=name,
        image_name='runpod/base:latest',
        gpu_type_id=gpu_type,
        min_vcpu_count=4*gpu_quantity,
        min_memory_in_gb=gpu_specs['memoryInGb']*gpu_quantity,
        country_code=region,
