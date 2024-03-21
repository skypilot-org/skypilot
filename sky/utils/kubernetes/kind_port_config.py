"""Fucntions to help set Kubernetes port config when creating Kind cluster"""

import argparse
import os

from sky import skypilot_config
from sky import sky_logging
from sky.utils import common_utils
from sky.utils import kubernetes_enums

CONFIG_PATH = "~/.sky/config.yaml"
BACKUP_PATH = "~/.sky/backup_config.yaml"

logger = sky_logging.init_logger(__name__)

def modify_port_config():
    """Set the `kubernetes:ports` to be `ingress` and backup the original value.
    
    If there is currently no config file, we then create the ~/.sky/config.yaml file
    and set the `kubernetes:ports` value to `loadbalancer`. We then store the current value
    (either `ingress` or `loadbalancer`) to the backup file at ~/.sky/backup_config.yaml, and
    finally set the value to `ingress` in the active config file.
    """
    
    if not skypilot_config.loaded_config_path():
        logger.debug(f'Making base {CONFIG_PATH} file as it did not previously exist.')
        default_config = {'kubernetes': {'ports': kubernetes_enums.KubernetesPortMode.LOADBALANCER.value}}
        config_path = os.path.expanduser(CONFIG_PATH)
        common_utils.dump_yaml(config_path, default_config)
        skypilot_config._try_load_config()
    
    logger.debug(f'Creating backup at {BACKUP_PATH} of current kubernetes port forwarding option.')
    backup_path = os.path.expanduser(BACKUP_PATH)
    backup_port_config = skypilot_config.get_nested(
        ('kubernetes', 'ports'),
        kubernetes_enums.KubernetesPortMode.LOADBALANCER.value)
    common_utils.dump_yaml(backup_path, {'kubernetes': {'ports': backup_port_config}})
    
    current_path = skypilot_config.loaded_config_path()
    logger.debug(f'Set current port forwarding option to ingress in {current_path}.')
    updated_config = skypilot_config.set_nested(('kubernetes', 'ports'), kubernetes_enums.KubernetesPortMode.INGRESS.value)
    common_utils.dump_yaml(current_path, updated_config)
    skypilot_config._try_load_config()


def restore_port_config():
    """Restore the original value of `kubernetes:ports`.
    
    Restores the value of `kubernetes:ports` from ~/.sky/backup_config.yaml in the active
    config file. Then, clears the ~/.sky/backup_config.yaml file.
    """

    backup_path = os.path.expanduser(BACKUP_PATH)
    try:
        backup_port_config = common_utils.read_yaml(backup_path)['kubernetes']['ports']
    except FileNotFoundError:
        logger.debug(f'Could not find backup file at {BACKUP_PATH}.')
        return
    except (TypeError, KeyError):
        logger.debug(f'Could not find `kubernetes:ports` in {BACKUP_PATH}.')
        return
    
    current_path = skypilot_config.loaded_config_path()
    if current_path is None:
        logger.debug('There is no current active config file.')
        return
    
    logger.debug(f'Restore original port forwarding option in {current_path} from {BACKUP_PATH}.')
    restored_config = skypilot_config.set_nested(('kubernetes', 'ports'), backup_port_config)
    common_utils.dump_yaml(current_path, restored_config)
    logger.debug(f'Resetting {BACKUP_PATH} to be empty.')
    common_utils.dump_yaml(backup_path, {})
    skypilot_config._try_load_config()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--modify',
                        action='store_true',
                        help='Backup current port config and set current value to ingress')
    parser.add_argument('--restore',
                        action='store_true',
                        help='Restore the port config from backup')
    args = parser.parse_args()

    if args.modify:
        modify_port_config()
    elif args.restore:
        restore_port_config()


if __name__ == '__main__':
    main()
