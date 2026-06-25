"""Tests for cloud instance link generation."""

from sky.provision import common
from sky.utils import instance_links


def _cluster_info(provider_name: str,
                  provider_config: dict) -> common.ClusterInfo:
    return common.ClusterInfo(
        instances={},
        head_instance_id=None,
        provider_name=provider_name,
        provider_config=provider_config,
    )


def test_generate_gcp_link_uses_cluster_name_on_cloud_override():
    cluster_info = _cluster_info('gcp', {'project_id': 'my-project'})

    links = instance_links.generate_instance_links(
        cluster_info=cluster_info,
        cluster_name='local-cluster-name',
        cluster_name_on_cloud='cloud-cluster-tag',
    )

    gcp_link = links['GCP Instances']
    assert 'project=my-project' in gcp_link
    assert '_3Acloud-cluster-tag_5C_22' in gcp_link
    assert '_3Alocal-cluster-name_5C_22' not in gcp_link


def test_generate_aws_link_uses_cluster_name_on_cloud_override():
    cluster_info = _cluster_info('aws', {'region': 'us-east-1'})

    links = instance_links.generate_instance_links(
        cluster_info=cluster_info,
        cluster_name='local-cluster-name',
        cluster_name_on_cloud='cloud-cluster-tag',
    )

    assert links['AWS Instances'].endswith(
        '#Instances:tag:ray-cluster-name=cloud-cluster-tag')


def test_generate_link_falls_back_to_cluster_name_without_override():
    cluster_info = _cluster_info('gcp', {'project_id': 'my-project'})

    links = instance_links.generate_instance_links(
        cluster_info=cluster_info,
        cluster_name='local-cluster-name',
    )

    assert '_3Alocal-cluster-name_5C_22' in links['GCP Instances']
