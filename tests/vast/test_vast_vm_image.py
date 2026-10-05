"""A Vast launch with no image must boot a KVM VM, not a Docker container.

Vast only launches a VM for images from docker.io/vastai/kvm. The previous
default ('vastai/base:0.0.2') started an unprivileged container on a
vms_enabled host (measured 2026-10-01), where the VM bootstrap cannot run.
"""
import unittest
from unittest import mock

from sky.clouds import vast as vast_cloud


class _Resources:
    """The slice of sky.Resources make_deploy_resources_variables reads.
    (mock.Mock refuses attribute names starting with 'assert'.)"""

    instance_type = '1x-RTX_PRO_6000_Max-Q-32-65536'
    region = 'EU'
    cluster_config_overrides: dict = {}

    def __init__(self, image_id):
        self.image_id = image_id

    def assert_launchable(self):
        return self

    def extract_docker_image(self):
        if self.image_id and self.image_id['EU'].startswith('docker:'):
            return self.image_id['EU'].split('docker:', 1)[1]
        return None


def _deploy_vars(image_id):
    resources = _Resources(image_id)
    region = mock.Mock()
    region.name = 'EU'
    cloud = vast_cloud.Vast()
    with mock.patch.object(cloud, 'get_accelerators_from_instance_type',
                           return_value={'RTXPRO6000': 1}):
        return cloud.make_deploy_resources_variables(resources,
                                                     mock.Mock(),
                                                     region,
                                                     zones=None,
                                                     num_nodes=1)


class TestVastVmImage(unittest.TestCase):

    def test_no_image_deploys_a_kvm_vm_image(self):
        image = _deploy_vars(None)['image_id']
        self.assertTrue(image.startswith('docker.io/vastai/kvm:'), image)

    def test_explicit_docker_image_still_wins(self):
        image = _deploy_vars({'EU': 'docker:ghcr.io/acme/worker:1'})['image_id']
        self.assertEqual(image, 'ghcr.io/acme/worker:1')



class TestVastVmLaunch(unittest.TestCase):
    """A KVM VM launch needs a shebang onstart and direct SSH."""

    def _launch_params(self, image):
        from sky.provision.vast import utils
        captured = {}
        client = mock.Mock()
        client.search_offers.return_value = [{
            'id': 7,
            'geolocation': 'Czechia, CZ, EU'
        }]
        client.create_instance.side_effect = (
            lambda **kw: captured.update(kw) or {'new_contract': 9})
        client.show_instance.return_value = {'id': 9}
        with mock.patch.object(utils, 'vast') as vast_mod:
            vast_mod.vast.return_value = client
            utils.launch(name='c',
                         instance_type='1x-RTX_PRO_6000_Max-Q-32-65536',
                         region='EU',
                         disk_size=256,
                         image_name=image,
                         ports=[22],
                         preemptible=False,
                         secure_only=True,
                         ssh_public_key='ssh-rsa AAAAtest controller')
        return captured

    def test_vm_image_gets_shebang_onstart_and_direct_ssh(self):
        params = self._launch_params(vast_cloud.DEFAULT_VM_IMAGE)
        self.assertTrue(params['onstart_cmd'].startswith('#!/bin/bash\n'),
                        params['onstart_cmd'][:40])
        self.assertIn('ssh-rsa AAAAtest controller', params['onstart_cmd'])
        self.assertIs(params.get('ssh'), True)
        self.assertIs(params.get('direct'), True)

    def test_container_image_keeps_one_line_onstart(self):
        params = self._launch_params('ghcr.io/acme/worker:1')
        self.assertFalse(params['onstart_cmd'].startswith('#!'))
        self.assertNotIn('\n', params['onstart_cmd'])
        self.assertNotIn('direct', params)


class TestVastSshEndpoint(unittest.TestCase):
    """SSH must use the direct IP with the direct port, or the proxy host with
    the proxy port -- never a mix."""

    def _endpoint(self, info):
        from sky.provision.vast import instance
        base = {'local_ipaddrs': '10.0.0.2 ', 'name': 'c-head'}
        base.update(info)
        with mock.patch.object(instance, '_filter_instances',
                               return_value={'1': base}):
            ci = instance.get_cluster_info('EU', 'c')
        node = ci.instances['1'][0]
        return node.external_ip, node.ssh_port

    def test_direct_mapping_uses_public_ip_and_mapped_port(self):
        self.assertEqual(
            self._endpoint({
                'public_ipaddr': '194.228.55.129',
                'ports': {'22/tcp': [{'HostPort': '39068'}]},
                'ssh_host': 'ssh2.vast.ai',
                'ssh_port': 23248,
            }), ('194.228.55.129', 39068))

    def test_no_direct_mapping_uses_proxy_pair(self):
        self.assertEqual(
            self._endpoint({
                'public_ipaddr': '194.228.55.129',
                'ports': None,
                'ssh_host': 'ssh2.vast.ai',
                'ssh_port': 23248,
            }), ('ssh2.vast.ai', 23248))


if __name__ == '__main__':
    unittest.main()
