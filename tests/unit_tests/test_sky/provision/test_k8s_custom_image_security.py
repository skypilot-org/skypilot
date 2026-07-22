"""Tests for Kubernetes custom-image security context handling (issue #8566)."""
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.skylet import constants as skylet_constants


def _minimal_cluster_yaml():
    return {
        'available_node_types': {
            'ray_head_default': {
                'node_config': {
                    'spec': {
                        'containers': [{
                            'name': 'ray-node',
                            'image': ('nvcr.io/nvidia/ai-dynamo/'
                                      'sglang-runtime:0.7.1'),
                        }],
                    },
                },
            },
        },
    }


def test_ensure_custom_image_sets_run_as_root():
    cluster_yaml = _minimal_cluster_yaml()
    kubernetes_utils.ensure_custom_image_container_runs_as_root(cluster_yaml)
    sec = cluster_yaml['available_node_types']['ray_head_default'][
        'node_config']['spec']['containers'][0]['securityContext']
    assert sec['runAsUser'] == 0
    assert sec['runAsGroup'] == 0


def test_ensure_custom_image_respects_existing_run_as_user():
    cluster_yaml = _minimal_cluster_yaml()
    container = cluster_yaml['available_node_types']['ray_head_default'][
        'node_config']['spec']['containers'][0]
    container['securityContext'] = {'runAsUser': 1000}
    kubernetes_utils.ensure_custom_image_container_runs_as_root(cluster_yaml)
    assert container['securityContext']['runAsUser'] == 1000
    assert 'runAsGroup' not in container['securityContext']


def test_ensure_custom_image_handles_null_security_context():
    cluster_yaml = _minimal_cluster_yaml()
    container = cluster_yaml['available_node_types']['ray_head_default'][
        'node_config']['spec']['containers'][0]
    container['securityContext'] = None
    kubernetes_utils.ensure_custom_image_container_runs_as_root(cluster_yaml)
    assert container['securityContext']['runAsUser'] == 0


def test_ensure_custom_image_handles_null_containers_list():
    cluster_yaml = _minimal_cluster_yaml()
    cluster_yaml['available_node_types']['ray_head_default']['node_config'][
        'spec']['containers'] = None
    kubernetes_utils.ensure_custom_image_container_runs_as_root(cluster_yaml)


def test_ensure_custom_image_respects_run_as_non_root():
    cluster_yaml = _minimal_cluster_yaml()
    container = cluster_yaml['available_node_types']['ray_head_default'][
        'node_config']['spec']['containers'][0]
    container['securityContext'] = {'runAsNonRoot': True}
    kubernetes_utils.ensure_custom_image_container_runs_as_root(cluster_yaml)
    assert 'runAsUser' not in container['securityContext']


def test_prefix_cmd_snippet_checks_sudo_availability():
    assert 'command -v sudo' in skylet_constants.PREFIX_CMD_SHELL_SNIPPET


def test_require_root_or_sudo_snippet_exits_without_privilege():
    assert 'exit 1' in skylet_constants.REQUIRE_ROOT_OR_SUDO_SHELL_SNIPPET
    snippet = skylet_constants.REQUIRE_ROOT_OR_SUDO_SHELL_SNIPPET
    assert 'command -v sudo' in snippet
