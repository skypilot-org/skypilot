"""SCP configuration bootstrapping."""

import subprocess

from sky.clouds.utils import scp_utils
from sky.provision import common


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    """Bootstraps instances for the given cluster."""
    del cluster_name

    node_cfg = config.node_config
    zone_id = _get_zone_id(region)
    node_cfg['zone_id'] = zone_id

    docker_cfg = config.docker_config
    docker_cfg['imageId'] = node_cfg['imageId']
    docker_cfg['serviceZoneId'] = zone_id
    docker_cfg['serverType'] = node_cfg['InstanceType']
    docker_cfg['contractId'] = 'None'
    ssh_public_key = node_cfg['AuthorizedKey']
    docker_cfg['initialScript'] = _get_init_script(ssh_public_key)

    key_pair_id = _get_key_pair_id()
    miscellaneous = {
        'deletionProtectionEnabled': False,
        'keyPairId': key_pair_id,
        'blockStorage': {
            'blockStorageName': 'skystorage',
            'diskSize': node_cfg['diskSize'],
            'encryptEnabled': False,
            'productId': 'PRODUCT-sRlJ34iBr9hOxN9J5PrQxo'
        },
        'nic': {
            'natEnabled': True
        },
    }

    docker_cfg.update(miscellaneous)

    return config


def _get_zone_id(region_name: str):
    zone_contents = scp_utils.SCPClient().get_zones()
    zone_dict = {
        item['serviceZoneName']: item['serviceZoneId'] for item in zone_contents
    }
    return zone_dict[region_name]


def _get_init_script(ssh_public_key: str):
    init_script_content = _get_default_config_cmd() + _get_ssh_key_gen_cmd(
        ssh_public_key)
    init_script_content_string = f'"{init_script_content}"'
    command = f'echo {init_script_content_string} | base64'
    result = subprocess.run(command,
                            shell=True,
                            capture_output=True,
                            text=True,
                            check=True)
    init_script_content_base64 = result.stdout
    return {
        'encodingType': 'base64',
        'initialScriptShell': 'bash',
        'initialScriptType': 'text',
        'initialScriptContent': init_script_content_base64
    }


def _get_default_config_cmd():
    cmd_list = ['apt-get update', 'apt-get -y install python3-pip']
    res = ''
    for cmd in cmd_list:
        res += cmd + '; '
    return res


def _get_ssh_key_gen_cmd(ssh_public_key: str):
    cmd_st = 'mkdir -p ~/.ssh/; touch ~/.ssh/authorized_keys;'
    cmd_ed = 'chmod 644 ~/.ssh/authorized_keys; chmod 700 ~/.ssh/'
    cmd = "echo '{}' &>>~/.ssh/authorized_keys;".format(ssh_public_key)  # pylint: disable=invalid-string-quote
    return cmd_st + cmd + cmd_ed


def _get_key_pair_id():
    key_pairs = scp_utils.SCPClient().get_key_pairs()
    if key_pairs['totalCount'] == 0:
        raise RuntimeError('create key pair')
    return key_pairs['contents'][0]['keyPairId']
