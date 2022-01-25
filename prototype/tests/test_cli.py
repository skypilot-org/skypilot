import click
import pytest

import sky
import sky.cli as cli


def test_infer_gpunode_type():
    resources = [
        sky.Resources(cloud=sky.AWS(), instance_type='p3.2xlarge'),
        sky.Resources(cloud=sky.GCP(), accelerators='K80'),
        sky.Resources(accelerators={'V100': 8}),
        sky.Resources(cloud=sky.Azure(), accelerators='A100'),
    ]
    for spec in resources:
        assert cli._infer_interactive_node_type(spec) == 'gpunode', spec


def test_infer_cpunode_type():
    resources = [
        sky.Resources(cloud=sky.AWS(), instance_type='m5.2xlarge'),
        sky.Resources(cloud=sky.GCP()),
        sky.Resources(),
    ]
    for spec in resources:
        assert cli._infer_interactive_node_type(spec) == 'cpunode', spec


def test_infer_tpunode_type():
    resources = [
        sky.Resources(cloud=sky.GCP(), accelerators='tpu-v3-8'),
        sky.Resources(cloud=sky.GCP(), accelerators='tpu-v2-32'),
        sky.Resources(cloud=sky.GCP(),
                      accelerators={'tpu-v2-128': 1},
                      accelerator_args={'tpu_name': 'tpu'}),
    ]
    for spec in resources:
        assert cli._infer_interactive_node_type(spec) == 'tpunode', spec


def test_default_resources_check():
    default_resources = cli._INTERACTIVE_NODE_DEFAULT_RESOURCES['gpunode']
    # sky gpunode --cloud aws --gpus V100
    resources = sky.Resources(cloud=sky.AWS(),
                              instance_type=default_resources.instance_type,
                              accelerators='V100',
                              use_spot=default_resources.use_spot)
    launched_resources = sky.Resources(cloud=sky.AWS(),
                                       instance_type='p3.2xlarge')
    cli._check_interactive_node_resources_match('gpunode',
                                                resources,
                                                launched_resources,
                                                user_requested_resources=True)
    # sky gpunode
    cli._check_interactive_node_resources_match('gpunode',
                                                default_resources,
                                                launched_resources,
                                                user_requested_resources=False)

    # sky gpunode --cloud aws -t p3.2xlarge
    requested_resources = sky.Resources(cloud=sky.AWS(),
                                        instance_type='p3.2xlarge')
    cli._check_interactive_node_resources_match('gpunode',
                                                requested_resources,
                                                launched_resources,
                                                user_requested_resources=True)


def test_resource_mismatch_check():
    default_resources = cli._INTERACTIVE_NODE_DEFAULT_RESOURCES['gpunode']
    # Launched resources from running: sky gpunode --cloud aws --gpus V100
    launched_resources = sky.Resources(cloud=sky.AWS(),
                                       instance_type='p3.2xlarge')

    requested_resources = [
        # sky gpunode --cloud gcp
        sky.Resources(cloud=sky.GCP(),
                      instance_type=default_resources.instance_type,
                      accelerators=default_resources.accelerators,
                      use_spot=default_resources.use_spot),

        # sky gpunode --gpus K80
        sky.Resources(cloud=default_resources.cloud,
                      instance_type=default_resources.instance_type,
                      accelerators='K80',
                      use_spot=default_resources.use_spot)
    ]
    for spec in requested_resources:
        with pytest.raises(click.UsageError) as e:
            cli._check_interactive_node_resources_match(
                'gpunode',
                spec,
                launched_resources,
                user_requested_resources=True)
        assert 'Resources cannot change for an existing cluster' in str(e.value)


def test_node_type_check():
    cnode_defaults = cli._INTERACTIVE_NODE_DEFAULT_RESOURCES['cpunode']
    tnode_defaults = cli._INTERACTIVE_NODE_DEFAULT_RESOURCES['tpunode']
    # Launched resources from running: sky gpunode -c t1 --cloud aws --gpus V100
    launched_resources = sky.Resources(cloud=sky.AWS(),
                                       instance_type='p3.2xlarge')

    requested_resources = [
        # sky cpunode -c t1
        ('cpunode',
         sky.Resources(cloud=cnode_defaults.cloud,
                       instance_type=cnode_defaults.instance_type,
                       accelerators=cnode_defaults.accelerators,
                       use_spot=cnode_defaults.use_spot)),
        # sky tpunode -c t1
        ('tpunode',
         sky.Resources(cloud=sky.GCP(),
                       instance_type=tnode_defaults.instance_type,
                       accelerators=tnode_defaults.accelerators,
                       use_spot=tnode_defaults.use_spot)),
    ]
    for requested_node_type, spec in requested_resources:
        with pytest.raises(click.UsageError) as e:
            cli._check_interactive_node_resources_match(
                requested_node_type,
                spec,
                launched_resources,
                user_requested_resources=False)
        assert 'Resources cannot change for an existing cluster' in str(e.value)
