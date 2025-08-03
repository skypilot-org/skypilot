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
        # Test underscore-based GPU names (CoreWeave format)
        (2, 8, 1, "H100_NVLINK_80GB", "2CPU--8GB--H100_NVLINK_80GB:1"),
        (8, 32, 4, "A100_SXM4_80GB", "8CPU--32GB--A100_SXM4_80GB:4"),
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

    # Backward compatibility test
    # Before https://github.com/skypilot-org/skypilot/pull/4756, the
    # accelerators are appended with format "--{a}{type}",
    # e.g. "4CPU--16GB--1V100".
    # TODO(romilb): Backward compatibility, remove after 0.11.0.
    prev_instance_type_name = '4CPU--16GB--1V100'
    assert KubernetesInstanceType.is_valid_instance_type(
        prev_instance_type_name
    ), f'Failed valid instance type check for {prev_instance_type_name}'
    cpus, memory, accelerator_count, accelerator_type = KubernetesInstanceType._parse_instance_type(
        prev_instance_type_name)
    assert cpus == 4, f'Failed parse check for {prev_instance_type_name}'
    assert memory == 16, f'Failed parse check for {prev_instance_type_name}'
    assert accelerator_count == 1, f'Failed parse check for {prev_instance_type_name}'
    assert accelerator_type == 'V100', f'Failed parse check for {prev_instance_type_name}'


def test_gpu_name_underscore_preservation():
    """Test that GPU names with underscores are preserved exactly as-is.
    
    This specifically tests the fix for CoreWeave H100_NVLINK_80GB support
    where underscores were incorrectly converted to spaces during parsing.
    """
    test_cases = [
        # (accelerator_name, expected_preserved_name)
        ("H100_NVLINK_80GB", "H100_NVLINK_80GB"),
        ("A100_SXM4_80GB", "A100_SXM4_80GB"),
        ("RTX4090", "RTX4090"),
        # Also test hyphen-based names continue to work
        ("H100-80GB", "H100-80GB"),
        ("A100-40GB", "A100-40GB"),
    ]
    
    for original_name, expected_name in test_cases:
        # Create instance type with accelerator name
        instance = KubernetesInstanceType.from_resources(
            cpus=4, memory=16, accelerator_count=1, accelerator_type=original_name
        )
        
        # Parse it back from the instance type string
        parsed_instance = KubernetesInstanceType.from_instance_type(instance.name)
        
        # Verify the accelerator name is preserved exactly
        assert parsed_instance.accelerator_type == expected_name, (
            f"Expected accelerator name '{expected_name}' but got "
            f"'{parsed_instance.accelerator_type}' after round-trip parsing"
        )
        
        # Verify the full instance type string contains the original name
        assert original_name in instance.name, (
            f"Instance type string '{instance.name}' should contain "
            f"original accelerator name '{original_name}'"
        )
