"""Runs `ray up` while not using ssh_proxy_command in launch hash.

This monkey patches the hash_launch_conf() function inside Ray autoscaler to
exclude any ssh_proxy_command in hash calculation.

Reasons:
 - In the future, we want to support changing the ssh_proxy_command field for
   an existing cluster. If the launch hash included this field, then this would
   mean upon such a change a new cluster would've been launched, causing
   leakage.
 - With our patch, ssh_proxy_command will be excluded from the launch hash when
   a cluster is first created. This then makes it possible for us to support
   changing the proxy command in the future.
"""
import hashlib
import json
import os

from ray.autoscaler import sdk


# Ref: https://github.com/ray-project/ray/blob/releases/2.2.0/python/ray/autoscaler/_private/util.py#L392-L404
def monkey_patch_hash_launch_conf(node_conf, auth):
    hasher = hashlib.sha1()
    # For hashing, we replace the path to the key with the key
    # itself. This is to make sure the hashes are the same even if keys
    # live at different locations on different machines.
    full_auth = auth.copy()
    full_auth.pop('ssh_proxy_command', None)  # NOTE: skypilot changes.
    for key_type in ['ssh_private_key', 'ssh_public_key']:
        if key_type in auth:
            with open(os.path.expanduser(auth[key_type])) as key:
                full_auth[key_type] = key.read()
    hasher.update(
        json.dumps([node_conf, full_auth], sort_keys=True).encode('utf-8'))
    return hasher.hexdigest()


# Since hash_launch_conf is imported this way, we must patch this imported
# version.
sdk.sdk.commands.hash_launch_conf = monkey_patch_hash_launch_conf
sdk.create_or_update_cluster({ray_yaml_path}, **{ray_up_kwargs})
