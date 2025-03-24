"""Tests for instance type in Kubernetes.

Tests verify correct instance type parsing and formatting.
"""
import pytest

from sky.provision.kubernetes.utils import KubernetesInstanceType


# Unit test for KubernetesInstanceType
def test_kubernetes_instance_type():
    test_cases = [
        (4, 16, None, None, "4CPU--16GB"),
        (0.5, 1.5, None, None, "0.5CPU--1.5GB"),
        (4, 16, 1, "V100", "4CPU--16GB--V100:1"),
        (4, 16, 2, "Atx100", "4CPU--16GB--Atx100:2"),
        (4, 16, 4, "4090", "4CPU--16GB--4090:4"),
        (4, 16, 1, "H100-80GB", "4CPU--16GB--H100-80GB:1"),
        (1, 6, 1, "K80", "1CPU--6GB--K80:1"),
    ]

    for cpus, memory, accelerator_count, accelerator_type, expected in test_cases:
        instance_type = KubernetesInstanceType(
            cpus=cpus,
            memory=memory,
            accelerator_count=accelerator_count,
            accelerator_type=accelerator_type)

        assert instance_type.name == expected, f'Failed name check for {cpus}, {memory}, {accelerator_count}, {accelerator_type}'

        assert KubernetesInstanceType.is_valid_instance_type(
            instance_type.name
        ), f'Failed valid instance type check for {cpus}, {memory}, {accelerator_count}, {accelerator_type}'

        cpus, memory, accelerator_count, accelerator_type = KubernetesInstanceType._parse_instance_type(
            instance_type.name)
        assert cpus == cpus, f'Failed parse check for {cpus}, {memory}, {accelerator_count}, {accelerator_type}'
        assert memory == memory, f'Failed parse check for {cpus}, {memory}, {accelerator_count}, {accelerator_type}'
        assert accelerator_count == accelerator_count, f'Failed parse check for {cpus}, {memory}, {accelerator_count}, {accelerator_type}'
        assert accelerator_type == accelerator_type, f'Failed parse check for {cpus}, {memory}, {accelerator_count}, {accelerator_type}'

        instance_type_from_name = KubernetesInstanceType.from_instance_type(
            instance_type.name)
        assert instance_type_from_name.cpus == cpus, f'Failed from instance type check for {cpus}, {memory}, {accelerator_count}, {accelerator_type}'
        assert instance_type_from_name.memory == memory, f'Failed from instance type check for {cpus}, {memory}, {accelerator_count}, {accelerator_type}'
        assert instance_type_from_name.accelerator_count == accelerator_count, f'Failed from instance type check for {cpus}, {memory}, {accelerator_count}, {accelerator_type}'
        assert instance_type_from_name.accelerator_type == accelerator_type, f'Failed from instance type check for {cpus}, {memory}, {accelerator_count}, {accelerator_type}'

        if accelerator_count is not None:
            instance_type_from_resources = KubernetesInstanceType.from_resources(
                cpus, memory, accelerator_count, accelerator_type)
            assert instance_type_from_resources.cpus == cpus, f'Failed from resources check for {cpus}, {memory}, {accelerator_count}, {accelerator_type}'
            assert instance_type_from_resources.memory == memory, f'Failed from resources check for {cpus}, {memory}, {accelerator_count}, {accelerator_type}'
            assert instance_type_from_resources.accelerator_count == accelerator_count, f'Failed from resources check for {cpus}, {memory}, {accelerator_count}, {accelerator_type}'
            assert instance_type_from_resources.accelerator_type == accelerator_type, f'Failed from resources check for {cpus}, {memory}, {accelerator_count}, {accelerator_type}'
