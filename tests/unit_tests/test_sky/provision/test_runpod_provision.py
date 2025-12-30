"""Unit tests for RunPod provisioning with allowed_cuda_versions."""

import pytest

from sky.provision.runpod import utils


class TestRunPodLaunchWithCudaVersions:
    """Tests for RunPod launch function with allowed_cuda_versions parameter."""

    def test_launch_gpu_with_cuda_versions(self, monkeypatch):
        """Test that allowed_cuda_versions is passed to GPU pod creation."""

        # Mock RunPod SDK
        created_params = {}

        class _MockRunPod:

            class runpod:

                @staticmethod
                def get_gpu(gpu_type):
                    return {'memoryInGb': 40}

                @staticmethod
                def create_pod(**kwargs):
                    created_params.update(kwargs)
                    return {'id': 'test-pod-id'}

        monkeypatch.setattr('sky.adaptors.runpod', _MockRunPod)
        monkeypatch.setattr('sky.provision.runpod.utils.runpod', _MockRunPod)

        # Mock template creation
        monkeypatch.setattr(
            'sky.provision.runpod.utils._create_template_for_docker_login',
            lambda *args, **kwargs: ('runpod/base:1.0.2', None))

        # Call launch with allowed_cuda_versions
        instance_id = utils.launch(
            cluster_name='test',
            node_type='head',
            instance_type='1x_A100-80GB_SECURE',
            region='US',
            zone='US-OR-1',
            disk_size=50,
            image_name='runpod/base:1.0.2',
            ports=[22],
            public_key='ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ...',
            preemptible=False,
            bid_per_gpu=0.5,
            docker_login_config=None,
            allowed_cuda_versions=['12.4', '12.3'])

        # Verify the parameter was passed
        assert instance_id == 'test-pod-id'
        assert 'allowed_cuda_versions' in created_params
        assert created_params['allowed_cuda_versions'] == ['12.4', '12.3']
        # Verify other GPU-specific params
        assert created_params['gpu_type_id'] == 'NVIDIA A100 80GB PCIe'
        assert created_params['gpu_count'] == 1
        assert created_params['cloud_type'] == 'SECURE'

    def test_launch_gpu_without_cuda_versions(self, monkeypatch):
        """Test that GPU launch works without allowed_cuda_versions."""

        created_params = {}

        class _MockRunPod:

            class runpod:

                @staticmethod
                def get_gpu(gpu_type):
                    return {'memoryInGb': 40}

                @staticmethod
                def create_pod(**kwargs):
                    created_params.update(kwargs)
                    return {'id': 'test-pod-id-2'}

        monkeypatch.setattr('sky.adaptors.runpod', _MockRunPod)
        monkeypatch.setattr('sky.provision.runpod.utils.runpod', _MockRunPod)
        monkeypatch.setattr(
            'sky.provision.runpod.utils._create_template_for_docker_login',
            lambda *args, **kwargs: ('runpod/base:1.0.2', None))

        # Call launch without allowed_cuda_versions
        instance_id = utils.launch(
            cluster_name='test',
            node_type='head',
            instance_type='1x_A100-80GB_SECURE',
            region='US',
            zone='US-OR-1',
            disk_size=50,
            image_name='runpod/base:1.0.2',
            ports=[22],
            public_key='ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ...',
            preemptible=False,
            bid_per_gpu=0.5,
            docker_login_config=None,
            allowed_cuda_versions=None)

        # Verify the parameter was NOT passed when None
        assert instance_id == 'test-pod-id-2'
        assert 'allowed_cuda_versions' not in created_params

    def test_launch_gpu_with_default_cuda_version(self, monkeypatch):
        """Test that default CUDA version ['12.8'] is passed correctly."""

        created_params = {}

        class _MockRunPod:

            class runpod:

                @staticmethod
                def get_gpu(gpu_type):
                    return {'memoryInGb': 80}

                @staticmethod
                def create_pod(**kwargs):
                    created_params.update(kwargs)
                    return {'id': 'test-pod-id-default'}

        monkeypatch.setattr('sky.adaptors.runpod', _MockRunPod)
        monkeypatch.setattr('sky.provision.runpod.utils.runpod', _MockRunPod)
        monkeypatch.setattr(
            'sky.provision.runpod.utils._create_template_for_docker_login',
            lambda *args, **kwargs: ('runpod/base:1.0.2', None))

        # Call launch with default CUDA version ['12.8']
        instance_id = utils.launch(
            cluster_name='test',
            node_type='head',
            instance_type='1x_A100-80GB_SECURE',
            region='US',
            zone='US-OR-1',
            disk_size=50,
            image_name='runpod/base:1.0.2',
            ports=[22],
            public_key='ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ...',
            preemptible=False,
            bid_per_gpu=0.5,
            docker_login_config=None,
            allowed_cuda_versions=['12.8'])

        # Verify the default parameter was passed
        assert instance_id == 'test-pod-id-default'
        assert 'allowed_cuda_versions' in created_params
        assert created_params['allowed_cuda_versions'] == ['12.8']

    def test_launch_cpu_without_cuda_versions(self, monkeypatch):
        """Test that allowed_cuda_versions is NOT passed for CPU instances."""

        created_params = {}

        class _MockRunPod:

            class runpod:

                @staticmethod
                def create_pod(**kwargs):
                    created_params.update(kwargs)
                    return {'id': 'test-cpu-pod-id'}

        monkeypatch.setattr('sky.adaptors.runpod', _MockRunPod)
        monkeypatch.setattr('sky.provision.runpod.utils.runpod', _MockRunPod)
        monkeypatch.setattr(
            'sky.provision.runpod.utils._create_template_for_docker_login',
            lambda *args, **kwargs: ('runpod/base:1.0.2', None))

        # Call launch for CPU instance with allowed_cuda_versions
        # (should be ignored for CPU)
        instance_id = utils.launch(
            cluster_name='test',
            node_type='head',
            instance_type='cpu-4-8',
            region='US',
            zone='US-OR-1',
            disk_size=50,
            image_name='runpod/base:1.0.2',
            ports=[22],
            public_key='ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ...',
            preemptible=False,
            bid_per_gpu=0.0,
            docker_login_config=None,
            allowed_cuda_versions=['12.4'])  # Should be ignored

        # Verify the parameter was NOT passed for CPU
        assert instance_id == 'test-cpu-pod-id'
        assert 'allowed_cuda_versions' not in created_params
        # Verify it's a CPU instance
        assert created_params['instance_id'] == 'cpu-4-8'
        assert 'gpu_type_id' not in created_params

    def test_launch_spot_with_cuda_versions(self, monkeypatch):
        """Test that allowed_cuda_versions works for spot instances."""

        created_params = {}

        class _MockCommands:

            @staticmethod
            def create_spot_pod(**kwargs):
                created_params.update(kwargs)
                return {'id': 'test-spot-pod-id'}

        class _MockRunPod:

            class runpod:

                @staticmethod
                def get_gpu(gpu_type):
                    return {'memoryInGb': 40}

        monkeypatch.setattr('sky.adaptors.runpod', _MockRunPod)
        monkeypatch.setattr('sky.provision.runpod.utils.runpod', _MockRunPod)
        monkeypatch.setattr('sky.provision.runpod.utils.runpod_commands',
                            _MockCommands)
        monkeypatch.setattr(
            'sky.provision.runpod.utils._create_template_for_docker_login',
            lambda *args, **kwargs: ('runpod/base:1.0.2', None))

        # Call launch for spot instance
        instance_id = utils.launch(
            cluster_name='test',
            node_type='head',
            instance_type='1x_A100-80GB_SECURE',
            region='US',
            zone='US-OR-1',
            disk_size=50,
            image_name='runpod/base:1.0.2',
            ports=[22],
            public_key='ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ...',
            preemptible=True,  # Spot instance
            bid_per_gpu=0.5,
            docker_login_config=None,
            allowed_cuda_versions=['12.4', '12.3'])

        # Verify it works for spot instances
        assert instance_id == 'test-spot-pod-id'
        assert created_params['allowed_cuda_versions'] == ['12.4', '12.3']
        assert created_params['bid_per_gpu'] == 0.5

    def test_launch_multiple_gpus_with_cuda_versions(self, monkeypatch):
        """Test that allowed_cuda_versions works for multi-GPU instances."""

        created_params = {}

        class _MockRunPod:

            class runpod:

                @staticmethod
                def get_gpu(gpu_type):
                    return {'memoryInGb': 80}

                @staticmethod
                def create_pod(**kwargs):
                    created_params.update(kwargs)
                    return {'id': 'test-multi-gpu-pod-id'}

        monkeypatch.setattr('sky.adaptors.runpod', _MockRunPod)
        monkeypatch.setattr('sky.provision.runpod.utils.runpod', _MockRunPod)
        monkeypatch.setattr(
            'sky.provision.runpod.utils._create_template_for_docker_login',
            lambda *args, **kwargs: ('runpod/base:1.0.2', None))

        # Call launch for 4xA100 instance
        instance_id = utils.launch(
            cluster_name='test',
            node_type='head',
            instance_type='4x_A100-80GB_SECURE',
            region='US',
            zone='US-OR-1',
            disk_size=100,
            image_name='runpod/base:1.0.2',
            ports=[22],
            public_key='ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ...',
            preemptible=False,
            bid_per_gpu=0.5,
            docker_login_config=None,
            allowed_cuda_versions=['12.4'])

        # Verify it works for multi-GPU instances
        assert instance_id == 'test-multi-gpu-pod-id'
        assert created_params['allowed_cuda_versions'] == ['12.4']
        assert created_params['gpu_count'] == 4
        assert created_params['min_vcpu_count'] == 16  # 4 * 4
        assert created_params['min_memory_in_gb'] == 320  # 80 * 4
