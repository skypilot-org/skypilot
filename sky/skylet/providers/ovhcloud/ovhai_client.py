import os

from libcloud.compute.providers import get_driver
from libcloud.compute.types import Provider
import yaml

CREDENTIALS_PATH = '~/.config/openstack/clouds.yaml'
DEFAULT_IMAGE = "NVIDIA GPU Cloud (NGC)"
DEFAULT_PUBLIC_NETWORK = "Ext-Net"


class OVHCloudClient:

    def __init__(self, region: str):
        with open(os.path.expanduser(CREDENTIALS_PATH), 'r') as _f:
            data = yaml.safe_load(_f)
        ovhcloud_info = data['clouds']['ovhcloud']['auth']
        OpenStack = get_driver(Provider.OPENSTACK)
        os_username = ovhcloud_info["username"]
        os_password = ovhcloud_info["password"]
        os_tenant_name = ovhcloud_info["project_name"]
        os_tenant_id = ovhcloud_info["project_domain_id"]
        self.os_tenant_id = os_tenant_id
        region = region.lower()
        self.region = region

        self.client = OpenStack(
            os_username,
            os_password,
            ex_force_auth_url="https://auth.cloud.ovh.net/",
            ex_force_auth_version="3.x_password",
            ex_tenant_name=os_tenant_name,
            ex_force_base_url=
            f"https://compute.{region}.cloud.ovh.net/v2.1/{os_tenant_id}/",
        )

    def get_filtered_nodes(self, tag_filters):
        found_nodes = {}
        for node in self.client.list_nodes():
            if node.state == 'running':
                metadata = dict(node.extra['metadata'])
                good = True
                for tag_key, tag_value in tag_filters.items():
                    if tag_key not in metadata or metadata[tag_key] != tag_value:
                        good = False
                if good:
                    found_nodes[node.id] = node
        return found_nodes

    def get_node_details(self, node_id):
        return self.client.ex_get_node_details(node_id)

    def list_key_pairs(self):
        return self.client.list_key_pairs()

    def create_key_pair(self, key_name, key_content):
        return self.client.import_key_pair_from_string(key_name, key_content)

    def find_instance_size(self, size_name: str):
        sizes = self.client.list_sizes()
        target_size = [size for size in sizes if size.name == size_name]
        if target_size:
            return target_size[0]
        return None

    def find_instance_image(self, image_name=DEFAULT_IMAGE):
        images = self.client.list_images()
        target_image = [image for image in images if image.name == image_name
                       ][0]
        return target_image

    def find_instance_network(self, instance_network=DEFAULT_PUBLIC_NETWORK):
        networks = self.client.ex_list_networks()
        target_network = [
            network for network in networks if network.name == instance_network
        ]
        return target_network

    def set_metadata(self, node_id, metadata):
        self.client.ex_set_metadata(node_id, metadata)

    def list_images(self):
        return self.client.list_images()

    def create_node(self, *args, **kwargs):
        return self.client.create_node(*args, **kwargs)

    def destroy_node(self, *args, **kwargs):
        return self.client.destroy_node(*args, **kwargs)
