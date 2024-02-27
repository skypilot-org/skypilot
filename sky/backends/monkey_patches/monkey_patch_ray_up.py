"""Runs our monkey-patched ray up.

This monkey patches:
1. The hash_launch_conf() function inside Ray autoscaler to exclude any ssh_proxy_command
in hash calculation.

Reasons:
 - With our patch, ssh_proxy_command will be excluded from the launch hash when
   a cluster is first created. This then makes it possible for us to support
   changing the proxy command without changing the launch hash in the future.

2. The _should_create_new_head() function inside Ray autoscaler to avoid the ray up 
checking the launch hash when creating the head node.

Reasons:
 - With our patch, the ray up will always reuse the existing head node with the same cluster
   name, even if the launch hash is different to avoid resources leakage. The outer-level
   SkyPilot code will guarantee that reusing the existing head node is safe, as the ray yaml
   is generated by SkyPilot (not modified by the user), i.e. the case where the launch hash
   is used to detect a change in the node_config is not needed.

   For worker nodes, ray autoscaler will check the launch hash by calling
   ray.autoscaler._private.autoscaler.launch_config_ok(). We pass the
   disable_launch_config_check to the generated ray yaml (sky.templates.<cloud>-ray.yml.j2)
   to avoid the launch hash check for worker nodes. When restarting a multinode cluster,
   the logic for considering what worker nodes to restart/"reuse" is in node_provider.py's
   create_node(), the same code for deciding what head node to restart/"reuse".
"""
import hashlib
import json
import os

from ray.autoscaler import sdk


# Ref: https://github.com/ray-project/ray/blob/releases/2.4.0/python/ray/autoscaler/_private/util.py#L396-L408
def monkey_patch_hash_launch_conf(node_conf, auth):
    hasher = hashlib.sha1()
    # For hashing, we replace the path to the key with the key
    # itself. This is to make sure the hashes are the same even if keys
    # live at different locations on different machines.
    full_auth = auth.copy()
    full_auth.pop('ssh_proxy_command', None)  # NOTE: skypilot changes.
    for key_type in ['ssh_private_key', 'ssh_public_key']:
        if key_type in auth:
            with open(os.path.expanduser(auth[key_type]),
                      encoding='utf-8') as key:
                full_auth[key_type] = key.read()
    hasher.update(
        json.dumps([node_conf, full_auth], sort_keys=True).encode('utf-8'))
    return hasher.hexdigest()


# Ref: https://github.com/ray-project/ray/blob/releases/2.4.0/python/ray/autoscaler/_private/commands.py#L854-L912
def monkey_patch_should_create_new_head(
    head_node_id,
    new_launch_hash,
    new_head_node_type,
    provider,
) -> bool:
    if not head_node_id:
        # The print will be piped into a log file and not directly shown to the user.
        print('No head node exists, need to create it.')
        # No head node exists, need to create it.
        return True
    print('Skipped creating a new head node.')
    # SkyPilot: We don't need to check if the head node has the same launch hash, as
    # the upper-level code of SkyPilot will guarantee that the head node is always
    # up-to-date.
    return False


# Since hash_launch_conf is imported this way, we must patch this imported
# version.
sdk.sdk.commands.hash_launch_conf = monkey_patch_hash_launch_conf
sdk.sdk.commands._should_create_new_head = monkey_patch_should_create_new_head
sdk.create_or_update_cluster({ray_yaml_path}, **{ray_up_kwargs})
