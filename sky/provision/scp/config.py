"""SCP configuration bootstrapping."""

from requests import auth

from sky.provision.scp import utils
from sky.provision import common

client = utils.SCPClient()


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    """Bootstraps instances for the given cluster."""
    del cluster_name  # unused

    node_cfg = config.node_config
    zone_id = _get_zone_id(region)
    node_cfg['zone_id'] = zone_id

    authentication_cfg = config.authentication_config
    ssh_user = authentication_cfg['ssh_user']
    ssh_public_key = node_cfg['AuthorizedKey']

    docker_cfg = config.docker_config
    docker_cfg['imageId'] = node_cfg['imageId']
    docker_cfg['serviceZoneId'] = zone_id
    docker_cfg['serverType'] = node_cfg['InstanceType']
    docker_cfg['contractId'] = "None"
    initial_script = _get_vm_init_script(ssh_public_key)
    docker_cfg['initialScript'] = initial_script

    miscellaneous = {
        'deletionProtectionEnabled': False,
        'dnsEnabled': True,
        'osAdmin': {
            'osUserId': ssh_user,
            'osUserPassword': 'default!@&$351!'
        },
        'blockStorage': {
            'blockStorageName': 'skystorage',
            'diskSize': node_cfg['diskSize'],
            'encryptEnabled': False,
            'productId': 'PRODUCT-sRlJ34iBr9hOxN9J5PrQxo'
        },
        "nic": {
            "natEnabled": True
        },
    }

    docker_cfg.update(miscellaneous)

    return config


def _get_zone_id(region_name: str):
    zone_contents = client.get_zones()
    zone_dict = {
        item['serviceZoneName']: item['serviceZoneId'] for item in zone_contents
    }
    return zone_dict[region_name]


def _get_vm_init_script(ssh_public_key: str):
    import subprocess
    init_script_content = _get_default_config_cmd() + _get_ssh_key_gen_cmd(
        ssh_public_key)
    init_script_content_string = f'"{init_script_content}"'
    command = f'echo {init_script_content_string} | base64'
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    init_script_content_base64 = result.stdout
    return {
        "encodingType": "base64",
        "initialScriptShell": "bash",
        "initialScriptType": "text",
        "initialScriptContent": init_script_content_base64
    }


def _get_ssh_key_gen_cmd(ssh_public_key: str):
    cmd_st = "mkdir -p ~/.ssh/; touch ~/.ssh/authorized_keys;"
    cmd_ed = "chmod 644 ~/.ssh/authorized_keys; chmod 700 ~/.ssh/"
    cmd = "echo '{}' &>>~/.ssh/authorized_keys;".format(ssh_public_key)
    return cmd_st + cmd + cmd_ed


def _get_default_config_cmd():
    cmd_list = ["apt-get update", "apt-get -y install python3-pip"]
    res = ""
    for cmd in cmd_list:
        res += cmd + "; "
    return res
