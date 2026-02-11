"""This module contains schemas used to validate objects.

Schemas conform to the JSON Schema specification as defined at
https://json-schema.org/
"""
import enum
from typing import Any, Dict, List, Tuple

from sky.skylet import autostop_lib
from sky.skylet import constants
from sky.utils import kubernetes_enums


def _check_not_both_fields_present(field1: str, field2: str):
    return {
        'oneOf': [{
            'required': [field1],
            'not': {
                'required': [field2]
            }
        }, {
            'required': [field2],
            'not': {
                'required': [field1]
            }
        }, {
            'not': {
                'anyOf': [{
                    'required': [field1]
                }, {
                    'required': [field2]
                }]
            }
        }]
    }


_AUTOSTOP_SCHEMA = {
    'anyOf': [
        {
            # Use boolean to disable autostop completely, e.g.
            #   autostop: false
            'type': 'boolean',
        },
        {
            # Shorthand to set idle_minutes by directly specifying, e.g.
            #   autostop: 5
            'anyOf': [{
                'type': 'string',
                'pattern': constants.TIME_PATTERN,
                'minimum': 0,
            }, {
                'type': 'integer',
            }]
        },
        {
            'type': 'object',
            'required': [],
            'additionalProperties': False,
            'properties': {
                # TODO(luca): update field to use time units as well.
                'idle_minutes': {
                    'type': 'integer',
                    'minimum': 0,
                },
                'down': {
                    'type': 'boolean',
                },
                'wait_for': {
                    'type': 'string',
                    'case_insensitive_enum':
                        autostop_lib.AutostopWaitFor.supported_modes(),
                },
                'hook': {
                    'type': 'string',
                },
                'hook_timeout': {
                    'type': 'integer',
                    'minimum': 1,
                }
            },
        },
    ],
}


# Note: This is similar to _get_infra_pattern()
# but without the wildcard patterns.
def _get_volume_infra_pattern():
    # Building the regex pattern for the infra field
    # Format: cloud[/region[/zone]] or wildcards or kubernetes context
    # Match any cloud name (case insensitive)
    all_clouds = list(constants.ALL_CLOUDS)
    all_clouds.remove('kubernetes')
    cloud_pattern = f'(?i:({"|".join(all_clouds)}))'

    # Optional /region followed by optional /zone
    # /[^/]+ matches a slash followed by any characters except slash (region or
    # zone name)
    # The outer (?:...)? makes the entire region/zone part optional
    region_zone_pattern = '(?:/[^/]+(?:/[^/]+)?)?'

    # Kubernetes specific pattern - matches:
    # 1. Just the word "kubernetes" or "k8s" by itself
    # 2. "k8s/" or "kubernetes/" followed by any context name (which may contain
    # slashes)
    kubernetes_pattern = '(?i:kubernetes|k8s)(?:/.+)?'

    # Combine all patterns with alternation (|)
    # ^ marks start of string, $ marks end of string
    infra_pattern = (f'^(?:{cloud_pattern}{region_zone_pattern}|'
                     f'{kubernetes_pattern})$')
    return infra_pattern


def _get_infra_pattern():
    # Building the regex pattern for the infra field
    # Format: cloud[/region[/zone]] or wildcards or kubernetes context
    # Match any cloud name (case insensitive)
    all_clouds = list(constants.ALL_CLOUDS)
    all_clouds.remove('kubernetes')
    cloud_pattern = f'(?i:({"|".join(all_clouds)}))'

    # Optional /region followed by optional /zone
    # /[^/]+ matches a slash followed by any characters except slash (region or
    # zone name)
    # The outer (?:...)? makes the entire region/zone part optional
    region_zone_pattern = '(?:/[^/]+(?:/[^/]+)?)?'

    # Wildcard patterns:
    # 1. * - any cloud
    # 2. */region - any cloud with specific region
    # 3. */*/zone - any cloud, any region, specific zone
    wildcard_cloud = '\\*'  # Wildcard for cloud
    wildcard_with_region = '(?:/[^/]+(?:/[^/]+)?)?'

    # Kubernetes specific pattern - matches:
    # 1. Just the word "kubernetes" or "k8s" by itself
    # 2. "k8s/" or "kubernetes/" followed by any context name (which may contain
    # slashes)
    kubernetes_pattern = '(?i:kubernetes|k8s)(?:/.+)?'

    # Combine all patterns with alternation (|)
    # ^ marks start of string, $ marks end of string
    infra_pattern = (f'^(?:{cloud_pattern}{region_zone_pattern}|'
                     f'{wildcard_cloud}{wildcard_with_region}|'
                     f'{kubernetes_pattern})$')
    return infra_pattern


def _get_single_resources_schema():
    """Schema for a single resource in a resources list."""
    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            'cloud': {
                'type': 'string',
                'case_insensitive_enum': list(constants.ALL_CLOUDS)
            },
            'region': {
                'type': 'string',
            },
            'zone': {
                'type': 'string',
            },
            'infra': {
                'type': 'string',
                'description':
                    ('Infrastructure specification in format: '
                     'cloud[/region[/zone]]. Use "*" as a wildcard.'),
                # Pattern validates:
                # 1. cloud[/region[/zone]] - e.g. "aws", "aws/us-east-1",
                #    "aws/us-east-1/us-east-1a"
                # 2. Wildcard patterns - e.g. "*", "*/us-east-1",
                #    "*/*/us-east-1a", "aws/*/us-east-1a"
                # 3. Kubernetes patterns - e.g. "kubernetes/my-context",
                #    "k8s/context-name",
                #    "k8s/aws:eks:us-east-1:123456789012:cluster/my-cluster"
                'pattern': _get_infra_pattern(),
            },
            'cpus': {
                'anyOf': [{
                    'type': 'string',
                }, {
                    'type': 'number',
                }],
            },
            'memory': {
                'anyOf': [{
                    'type': 'string',
                }, {
                    'type': 'number',
                }],
            },
            'accelerators': {
                'anyOf': [{
                    'type': 'string',
                }, {
                    'type': 'object',
                    'required': [],
                    'maxProperties': 1,
                    'additionalProperties': {
                        'type': 'number'
                    }
                }]
            },
            'instance_type': {
                'type': 'string',
            },
            'use_spot': {
                'type': 'boolean',
            },
            'job_recovery': {
                # Either a string or a dict.
                'anyOf': [
                    {
                        'type': 'string',
                    },
                    {
                        'type': 'object',
                        'required': [],
                        'additionalProperties': False,
                        'properties': {
                            'strategy': {
                                'anyOf': [{
                                    'type': 'string',
                                }, {
                                    'type': 'null',
                                }],
                            },
                            'max_restarts_on_errors': {
                                'type': 'integer',
                                'minimum': 0,
                            },
                            'recover_on_exit_codes': {
                                'anyOf': [
                                    {
                                        # Single exit code
                                        'type': 'integer',
                                        'minimum': 0,
                                        'maximum': 255,
                                    },
                                    {
                                        # List of exit codes
                                        'type': 'array',
                                        'items': {
                                            'type': 'integer',
                                            'minimum': 0,
                                            'maximum': 255,
                                        },
                                        'uniqueItems': True,
                                    },
                                ],
                            },
                        }
                    }
                ],
            },
            'volumes': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'disk_size': {
                            'anyOf': [{
                                'type': 'string',
                                'pattern': constants.MEMORY_SIZE_PATTERN,
                            }, {
                                'type': 'integer',
                            }],
                        },
                        'disk_tier': {
                            'type': 'string',
                        },
                        'path': {
                            'type': 'string',
                        },
                        'auto_delete': {
                            'type': 'boolean',
                        },
                        'storage_type': {
                            'type': 'string',
                        },
                        'name': {
                            'type': 'string',
                        },
                        'attach_mode': {
                            'type': 'string',
                        },
                    },
                },
            },
            'disk_size': {
                'anyOf': [{
                    'type': 'string',
                    'pattern': constants.MEMORY_SIZE_PATTERN,
                }, {
                    'type': 'integer',
                }],
            },
            'disk_tier': {
                'type': 'string',
            },
            'network_tier': {
                'type': 'string',
            },
            'local_disk': {
                'type': 'string',
            },
            'ports': {
                'anyOf': [{
                    'type': 'string',
                }, {
                    'type': 'integer',
                }, {
                    'type': 'array',
                    'items': {
                        'anyOf': [{
                            'type': 'string',
                        }, {
                            'type': 'integer',
                        }]
                    }
                }, {
                    'type': 'null',
                }],
            },
            'labels': {
                'type': 'object',
                'additionalProperties': {
                    'type': 'string'
                }
            },
            'accelerator_args': {
                'type': 'object',
                'required': [],
                'additionalProperties': False,
                'properties': {
                    'runtime_version': {
                        'type': 'string',
                    },
                    'tpu_name': {
                        'type': 'string',
                    },
                    'tpu_vm': {
                        'type': 'boolean',
                    },
                    'gcp_queued_resource': {
                        'type': 'boolean',
                    },
                }
            },
            '_no_missing_accel_warnings': {
                'type': 'boolean'
            },
            'image_id': {
                'anyOf': [{
                    'type': 'string',
                }, {
                    'type': 'object',
                    'required': [],
                }, {
                    'type': 'null',
                }]
            },
            'autostop': _AUTOSTOP_SCHEMA,
            'priority': {
                'type': 'integer',
                'minimum': constants.MIN_PRIORITY,
                'maximum': constants.MAX_PRIORITY,
            },
            # The following fields are for internal use only. Should not be
            # specified in the task config.
            '_docker_login_config': {
                'type': 'object',
                'required': ['username', 'password', 'server'],
                'additionalProperties': False,
                'properties': {
                    'username': {
                        'type': 'string',
                    },
                    'password': {
                        'type': 'string',
                    },
                    'server': {
                        'type': 'string',
                    }
                }
            },
            '_is_image_managed': {
                'type': 'boolean',
            },
            '_requires_fuse': {
                'type': 'boolean',
            },
            '_cluster_config_overrides': {
                'type': 'object',
            },
        }
    }


def _get_multi_resources_schema():
    multi_resources_schema = {
        k: v
        for k, v in _get_single_resources_schema().items()
        # Validation may fail if $schema is included.
        if k != '$schema'
    }
    return multi_resources_schema


def get_resources_schema():
    """Resource schema in task config."""
    single_resources_schema = _get_single_resources_schema()['properties']
    single_resources_schema.pop('accelerators')
    multi_resources_schema = _get_multi_resources_schema()
    return {
        '$schema': 'http://json-schema.org/draft-07/schema#',
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            **single_resources_schema,
            # We redefine the 'accelerators' field to allow one line list or
            # a set of accelerators.
            'accelerators': {
                # {'V100:1', 'A100:1'} will be
                # read as a string and converted to dict.
                'anyOf': [{
                    'type': 'string',
                }, {
                    'type': 'object',
                    'required': [],
                    'additionalProperties': {
                        'anyOf': [{
                            'type': 'null',
                        }, {
                            'type': 'number',
                        }]
                    }
                }, {
                    'type': 'array',
                    'items': {
                        'type': 'string',
                    }
                }]
            },
            'any_of': {
                'type': 'array',
                'items': multi_resources_schema,
            },
            'ordered': {
                'type': 'array',
                'items': multi_resources_schema,
            }
        },
    }


def get_volume_schema():
    # pylint: disable=import-outside-toplevel
    from sky.utils import volume

    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'type': 'object',
        'required': ['name', 'type'],
        'additionalProperties': False,
        'properties': {
            'name': {
                'type': 'string',
            },
            'type': {
                'type': 'string',
                'case_sensitive_enum': [
                    type.value for type in volume.VolumeType
                ],
            },
            'infra': {
                'type': 'string',
                'description': ('Infrastructure specification in format: '
                                'cloud[/region[/zone]].'),
                # Pattern validates:
                # 1. cloud[/region[/zone]] - e.g. "aws", "aws/us-east-1",
                #    "aws/us-east-1/us-east-1a"
                # 2. Kubernetes patterns - e.g. "kubernetes/my-context",
                #    "k8s/context-name",
                #    "k8s/aws:eks:us-east-1:123456789012:cluster/my-cluster"
                'pattern': _get_volume_infra_pattern(),
            },
            'size': {
                'type': 'string',
                'pattern': constants.MEMORY_SIZE_PATTERN,
            },
            'use_existing': {
                'type': 'boolean',
            },
            'config': {
                'type': 'object',
                'required': [],
                'properties': {
                    'storage_class_name': {
                        'type': 'string',
                    },
                    'access_mode': {
                        'type': 'string',
                        'case_sensitive_enum': [
                            type.value for type in volume.VolumeAccessMode
                        ],
                    },
                    'namespace': {
                        'type': 'string',
                    },
                },
            },
            **_LABELS_SCHEMA,
        }
    }


def get_storage_schema():
    # pylint: disable=import-outside-toplevel
    from sky.data import storage

    # Refer to https://rclone.org/docs/#options for more information
    # on rclone-specific nomenclature.
    rclone_memory_units = ('B', 'K', 'M', 'G', 'T', 'P')
    rclone_memory_pattern = (
        '^[0-9]+('
        f'{"|".join([unit.lower() for unit in rclone_memory_units])}|'
        f'{"|".join([unit.upper() for unit in rclone_memory_units])})?$')
    rclone_duration_pattern = (
        r'^(?:(?:[-+]?(?:\d+(?:\.\d+)?|\.\d+)'
        r'(?:ms|[smhdwMy]))+|([-+]?(?:\d+(?:\.\d+)?|\.\d+)))$')

    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            'name': {
                'type': 'string',
            },
            'source': {
                'anyOf': [{
                    'type': 'string',
                }, {
                    'type': 'array',
                    'minItems': 1,
                    'items': {
                        'type': 'string'
                    }
                }]
            },
            'store': {
                'type': 'string',
                'case_insensitive_enum': [
                    type.value for type in storage.StoreType
                ]
            },
            'persistent': {
                'type': 'boolean',
            },
            'mode': {
                'type': 'string',
                'case_insensitive_enum': [
                    mode.value for mode in storage.StorageMode
                ]
            },
            'config': {
                'type': 'object',
                'properties': {
                    'disk_size': {
                        'anyOf': [{
                            'type': 'string',
                            'pattern': constants.MEMORY_SIZE_PATTERN,
                        }, {
                            'type': 'integer',
                        }],
                    },
                    'disk_tier': {
                        'type': 'string',
                    },
                    'storage_type': {
                        'type': 'string',
                    },
                    'attach_mode': {
                        'type': 'string',
                    },
                    'mount_cached': {
                        'type': 'object',
                        'additionalProperties': False,
                        'properties': {
                            'transfers': {
                                'type': 'integer',
                                'minimum': 1,
                            },
                            'buffer_size': {
                                'type': 'string',
                                'pattern': rclone_memory_pattern,
                            },
                            'vfs_cache_max_size': {
                                'type': 'string',
                                'pattern': rclone_memory_pattern,
                            },
                            'vfs_cache_max_age': {
                                'type': 'string',
                                'pattern': rclone_duration_pattern,
                            },
                            'vfs_read_ahead': {
                                'type': 'string',
                                'pattern': rclone_memory_pattern,
                            },
                            'vfs_read_chunk_size': {
                                'type': 'string',
                                'pattern': rclone_memory_pattern,
                            },
                            'vfs_read_chunk_streams': {
                                'type': 'integer',
                                'minimum': 0,
                            },
                            'vfs_write_back': {
                                'type': 'string',
                                'pattern': rclone_duration_pattern,
                            },
                            'read_only': {
                                'type': 'boolean',
                            },
                        },
                    },
                },
            },
            '_is_sky_managed': {
                'type': 'boolean',
            },
            '_bucket_sub_path': {
                'type': 'string',
            },
            '_force_delete': {
                'type': 'boolean',
            }
        }
    }


def get_volume_mount_schema():
    """Schema for volume mount object in task config (internal use only)."""
    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            'path': {
                'type': 'string',
            },
            'volume_name': {
                'type': 'string',
            },
            'is_ephemeral': {
                'type': 'boolean',
            },
            'volume_config': {
                'type': 'object',
                'required': [],
                'additionalProperties': True,
                'properties': {
                    'cloud': {
                        'type': 'string',
                        'case_insensitive_enum': list(constants.ALL_CLOUDS)
                    },
                    'region': {
                        'anyOf': [{
                            'type': 'string'
                        }, {
                            'type': 'null'
                        }]
                    },
                    'zone': {
                        'anyOf': [{
                            'type': 'string'
                        }, {
                            'type': 'null'
                        }]
                    },
                },
            }
        }
    }


def get_service_schema():
    """Schema for top-level `service:` field (for SkyServe)."""
    # To avoid circular imports, only import when needed.
    # pylint: disable=import-outside-toplevel
    from sky.serve import load_balancing_policies
    from sky.serve import spot_placer
    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'readiness_probe': {
                'anyOf': [{
                    'type': 'string',
                }, {
                    'type': 'object',
                    'required': ['path'],
                    'additionalProperties': False,
                    'properties': {
                        'path': {
                            'type': 'string',
                        },
                        'initial_delay_seconds': {
                            'type': 'number',
                        },
                        'timeout_seconds': {
                            'type': 'number',
                        },
                        'post_data': {
                            'anyOf': [{
                                'type': 'string',
                            }, {
                                'type': 'object',
                            }]
                        },
                        'headers': {
                            'type': 'object',
                            'additionalProperties': {
                                'type': 'string'
                            }
                        },
                    }
                }]
            },
            'pool': {
                'type': 'object',
                'required': [],
                'additionalProperties': False,
                'properties': {
                    'workers': {
                        'type': 'integer',
                        'minimum': 0,
                    },
                    'min_workers': {
                        'type': 'integer',
                        'minimum': 0,
                    },
                    'queue_length_threshold': {
                        'type': 'integer',
                        'minimum': 1,
                    },
                    'max_workers': {
                        'type': 'integer',
                        'minimum': 0,
                    },
                    'upscale_delay_seconds': {
                        'type': 'number',
                        'minimum': 0,
                    },
                    'downscale_delay_seconds': {
                        'type': 'number',
                        'minimum': 0,
                    },
                },
            },
            'replica_policy': {
                'type': 'object',
                'required': ['min_replicas'],
                'additionalProperties': False,
                'properties': {
                    'min_replicas': {
                        'type': 'integer',
                        'minimum': 0,
                    },
                    'max_replicas': {
                        'type': 'integer',
                        'minimum': 0,
                    },
                    'num_overprovision': {
                        'type': 'integer',
                        'minimum': 0,
                    },
                    'target_qps_per_replica': {
                        'anyOf': [
                            {
                                'type': 'number',
                                'minimum': 0,
                            },
                            {
                                'type': 'object',
                                'patternProperties': {
                                    # Pattern for accelerator types like
                                    # "H100:1", "A100:1", "H100", "A100"
                                    '^[A-Z0-9]+(?::[0-9]+)?$': {
                                        'type': 'number',
                                        'minimum': 0,
                                    }
                                },
                                'additionalProperties': False,
                            }
                        ]
                    },
                    'dynamic_ondemand_fallback': {
                        'type': 'boolean',
                    },
                    'base_ondemand_fallback_replicas': {
                        'type': 'integer',
                        'minimum': 0,
                    },
                    'spot_placer': {
                        'type': 'string',
                        'case_insensitive_enum': list(
                            spot_placer.SPOT_PLACERS.keys())
                    },
                    'upscale_delay_seconds': {
                        'type': 'number',
                    },
                    'downscale_delay_seconds': {
                        'type': 'number',
                    },
                }
            },
            'ports': {
                'type': 'integer',
            },
            'replicas': {
                'type': 'integer',
            },
            'workers': {
                'type': 'integer',
            },
            'load_balancing_policy': {
                'type': 'string',
                'case_insensitive_enum': list(
                    load_balancing_policies.LB_POLICIES.keys())
            },
            'tls': {
                'type': 'object',
                'required': ['keyfile', 'certfile'],
                'additionalProperties': False,
                'properties': {
                    'keyfile': {
                        'type': 'string',
                    },
                    'certfile': {
                        'type': 'string',
                    },
                },
            },
        }
    }


def _filter_schema(schema: dict, keys_to_keep: List[Tuple[str, ...]]) -> dict:
    """Recursively filter a schema to include only certain keys.

    Args:
        schema: The original schema dictionary.
        keys_to_keep: List of tuples with the path of keys to retain.

    Returns:
        The filtered schema.
    """
    # Convert list of tuples to a dictionary for easier access
    paths_dict: Dict[str, Any] = {}
    for path in keys_to_keep:
        current = paths_dict
        for step in path:
            if step not in current:
                current[step] = {}
            current = current[step]

    def keep_keys(current_schema: dict, current_path_dict: dict,
                  new_schema: dict) -> dict:
        # Base case: if we reach a leaf in the path_dict, we stop.
        if (not current_path_dict or not isinstance(current_schema, dict) or
                not current_schema.get('properties')):
            return current_schema

        if 'properties' not in new_schema:
            new_schema = {
                key: current_schema[key]
                for key in current_schema
                # We do not support the handling of `oneOf`, `anyOf`, `allOf`,
                # `required` for now.
                if key not in
                {'properties', 'oneOf', 'anyOf', 'allOf', 'required'}
            }
            new_schema['properties'] = {}
        for key, sub_schema in current_schema['properties'].items():
            if key in current_path_dict:
                # Recursively keep keys if further path dict exists
                new_schema['properties'][key] = {}
                current_path_value = current_path_dict.pop(key)
                new_schema['properties'][key] = keep_keys(
                    sub_schema, current_path_value,
                    new_schema['properties'][key])

        return new_schema

    # Start the recursive filtering
    new_schema = keep_keys(schema, paths_dict, {})
    assert not paths_dict, f'Unprocessed keys: {paths_dict}'
    return new_schema


def get_task_schema():
    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            'name': {
                'type': 'string',
            },
            'workdir': {
                'anyOf': [{
                    'type': 'string',
                }, {
                    'type': 'object',
                    'required': ['url'],
                    'additionalProperties': False,
                    'properties': {
                        'url': {
                            'type': 'string',
                        },
                        'ref': {
                            'type': 'string',
                        },
                    },
                }],
            },
            'event_callback': {
                'type': 'string',
            },
            'num_nodes': {
                'type': 'integer',
            },
            # resources config is validated separately using RESOURCES_SCHEMA
            'resources': {
                'type': 'object',
            },
            # storage config is validated separately using STORAGE_SCHEMA
            'file_mounts': {
                'type': 'object',
            },
            # service config is validated separately using SERVICE_SCHEMA
            'service': {
                'type': 'object',
            },
            'pool': {
                'type': 'object',
            },
            'setup': {
                'type': 'string',
            },
            'run': {
                'type': 'string',
            },
            'envs': {
                'type': 'object',
                'required': [],
                'patternProperties': {
                    # Checks env keys are valid env var names.
                    '^[a-zA-Z_][a-zA-Z0-9_]*$': {
                        'type': ['string', 'null']
                    }
                },
                'additionalProperties': False,
            },
            'secrets': {
                'type': 'object',
                'required': [],
                'patternProperties': {
                    # Checks secret keys are valid env var names.
                    '^[a-zA-Z_][a-zA-Z0-9_]*$': {
                        'type': ['string', 'null']
                    }
                },
                'additionalProperties': False,
            },
            # inputs and outputs are experimental
            'inputs': {
                'type': 'object',
                'required': [],
                'maxProperties': 1,
                'additionalProperties': {
                    'type': 'number'
                }
            },
            'outputs': {
                'type': 'object',
                'required': [],
                'maxProperties': 1,
                'additionalProperties': {
                    'type': 'number'
                }
            },
            'file_mounts_mapping': {
                'type': 'object',
            },
            'config': _filter_schema(
                get_config_schema(),
                constants.OVERRIDEABLE_CONFIG_KEYS_IN_TASK),
            # volumes config is validated separately using get_volume_schema
            'volumes': {
                'type': 'object',
            },
            'volume_mounts': {
                'type': 'array',
                'items': get_volume_mount_schema(),
            },
            '_metadata': {
                'type': 'object',
            },
        }
    }


def get_cluster_schema():
    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'type': 'object',
        'required': ['cluster', 'auth'],
        'additionalProperties': False,
        'properties': {
            'cluster': {
                'type': 'object',
                'required': ['ips', 'name'],
                'additionalProperties': False,
                'properties': {
                    'ips': {
                        'type': 'array',
                        'items': {
                            'type': 'string',
                        }
                    },
                    'name': {
                        'type': 'string',
                    },
                }
            },
            'auth': {
                'type': 'object',
                'required': ['ssh_user', 'ssh_private_key'],
                'additionalProperties': False,
                'properties': {
                    'ssh_user': {
                        'type': 'string',
                    },
                    'ssh_private_key': {
                        'type': 'string',
                    },
                }
            },
            'python': {
                'type': 'string',
            },
        }
    }


_NETWORK_CONFIG_SCHEMA = {
    'use_internal_ips': {
        'type': 'boolean',
    },
    'ssh_proxy_command': {
        'oneOf': [{
            'type': 'string',
        }, {
            'type': 'null',
        }, {
            'type': 'object',
            'required': [],
            'additionalProperties': {
                'anyOf': [
                    {
                        'type': 'string'
                    },
                    {
                        'type': 'null'
                    },
                ]
            }
        }]
    },
}

_LABELS_SCHEMA = {
    'labels': {
        'type': 'object',
        'required': [],
        'additionalProperties': {
            'type': 'string',
        },
    }
}

_PROPERTY_NAME_OR_CLUSTER_NAME_TO_PROPERTY = {
    'oneOf': [
        {
            'type': 'string'
        },
        {
            # A list of single-element dict to pretain the
            # order.
            # Example:
            #  property_name:
            #    - my-cluster1-*: my-property-1
            #    - my-cluster2-*: my-property-2
            #    - "*"": my-property-3
            'type': 'array',
            'items': {
                'type': 'object',
                'additionalProperties': {
                    'type': 'string'
                },
                'maxProperties': 1,
                'minProperties': 1,
            },
        }
    ]
}


class RemoteIdentityOptions(enum.Enum):
    """Enum for remote identity types.

    Some clouds (e.g., AWS, Kubernetes) also allow string values for remote
    identity, which map to the service account/role to use. Those are not
    included in this enum.
    """
    LOCAL_CREDENTIALS = 'LOCAL_CREDENTIALS'
    SERVICE_ACCOUNT = 'SERVICE_ACCOUNT'
    NO_UPLOAD = 'NO_UPLOAD'


def get_default_remote_identity(cloud: str) -> str:
    """Get the default remote identity for the specified cloud."""
    if cloud in ('kubernetes', 'ssh'):
        return RemoteIdentityOptions.SERVICE_ACCOUNT.value
    return RemoteIdentityOptions.LOCAL_CREDENTIALS.value


_CAPABILITIES_SCHEMA = {
    'capabilities': {
        'type': 'array',
        'items': {
            'type': 'string',
            'case_insensitive_enum': ['compute', 'storage']
        },
    }
}

_REMOTE_IDENTITY_SCHEMA = {
    'remote_identity': {
        'type': 'string',
        'case_insensitive_enum': [
            option.value for option in RemoteIdentityOptions
        ]
    }
}

_REMOTE_IDENTITY_SCHEMA_KUBERNETES = {
    'remote_identity': {
        'anyOf': [{
            'type': 'string'
        }, {
            'type': 'object',
            'additionalProperties': {
                'type': 'string'
            }
        }]
    },
}

_CONTEXT_CONFIG_SCHEMA_MINIMAL = {
    'pod_config': {
        'type': 'object',
        'required': [],
        # Allow arbitrary keys since validating pod spec is hard
        'additionalProperties': True,
    },
    'provision_timeout': {
        'type': 'integer',
    },
    'custom_metadata': {
        'type': 'object',
        'required': [],
        # Allow arbitrary keys since validating metadata is hard
        'additionalProperties': True,
        # Disallow 'name' and 'namespace' keys in this dict
        'not': {
            'anyOf': [{
                'required': ['name']
            }, {
                'required': ['namespace']
            }]
        },
    },
}

_CONTEXT_CONFIG_SCHEMA_KUBERNETES = {
    # TODO(kevin): Remove 'networking' in v0.13.0.
    'networking': {
        'type': 'string',
        'case_insensitive_enum': [
            type.value for type in kubernetes_enums.KubernetesNetworkingMode
        ],
    },
    'ports': {
        'type': 'string',
        'case_insensitive_enum': [
            type.value for type in kubernetes_enums.KubernetesPortMode
        ],
    },
    **_CONTEXT_CONFIG_SCHEMA_MINIMAL,
    'autoscaler': {
        'type': 'string',
        'case_insensitive_enum': [
            type.value for type in kubernetes_enums.KubernetesAutoscalerType
        ],
    },
    'high_availability': {
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            'storage_class_name': {
                'type': 'string',
            }
        },
    },
    'kueue': {
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            'local_queue_name': {
                'type': 'string',
            },
        },
    },
    'dws': {
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            'enabled': {
                'type': 'boolean',
            },
            # Only used when Kueue is enabled.
            'max_run_duration': {
                'anyOf': [{
                    'type': 'string',
                    'pattern': constants.TIME_PATTERN,
                }, {
                    'type': 'integer',
                }]
            },
        },
    },
    'remote_identity': {
        'type': 'string',
    },
    'post_provision_runcmd': {
        'type': 'array',
        'items': {
            'type': 'string'
        },
    },
    'set_pod_resource_limits': {
        # Can be:
        # - false: do not set limits (default)
        # - true: set limits equal to requests (multiplier of 1)
        # - number: set limits to requests * multiplier
        'oneOf': [{
            'type': 'boolean',
        }, {
            'type': 'number',
            'minimum': 1,
        }],
    },
}


def get_config_schema():
    # pylint: disable=import-outside-toplevel
    from sky.server import daemons

    resources_schema = {
        k: v
        for k, v in get_resources_schema().items()
        # Validation may fail if $schema is included.
        if k != '$schema'
    }
    resources_schema['properties'].pop('ports')

    def _get_controller_schema():
        return {
            'type': 'object',
            'required': [],
            'additionalProperties': False,
            'properties': {
                'controller': {
                    'type': 'object',
                    'required': [],
                    'additionalProperties': False,
                    'properties': {
                        'resources': resources_schema,
                        'high_availability': {
                            'type': 'boolean',
                            'default': False,
                        },
                        'autostop': _AUTOSTOP_SCHEMA,
                        'consolidation_mode': {
                            'type': 'boolean',
                            'default': False,
                        },
                        'controller_logs_gc_retention_hours': {
                            'type': 'integer',
                        },
                        'task_logs_gc_retention_hours': {
                            'type': 'integer',
                        },
                    },
                },
                'bucket': {
                    'type': 'string',
                    'pattern': '^(https|s3|gs|r2|cos)://.+',
                    'required': [],
                },
                'force_disable_cloud_bucket': {
                    'type': 'boolean',
                    'default': False,
                },
            }
        }

    cloud_configs = {
        'aws': {
            'type': 'object',
            'required': [],
            'additionalProperties': False,
            'properties': {
                'prioritize_reservations': {
                    'type': 'boolean',
                },
                'specific_reservations': {
                    'type': 'array',
                    'items': {
                        'type': 'string',
                    },
                },
                'disk_encrypted': {
                    'type': 'boolean',
                },
                'ssh_user': {
                    'type': 'string',
                },
                'security_group_name':
                    (_PROPERTY_NAME_OR_CLUSTER_NAME_TO_PROPERTY),
                'vpc_name': {
                    'oneOf': [{
                        'type': 'string',
                    }, {
                        'type': 'null',
                    }]
                },
                'vpc_names': {
                    'oneOf': [{
                        'type': 'string',
                    }, {
                        'type': 'null',
                    }, {
                        'type': 'array',
                        'items': {
                            'type': 'string'
                        }
                    }],
                },
                'use_ssm': {
                    'type': 'boolean',
                },
                'post_provision_runcmd': {
                    'type': 'array',
                    'items': {
                        'oneOf': [{
                            'type': 'string'
                        }, {
                            'type': 'array',
                            'items': {
                                'type': 'string'
                            }
                        }]
                    },
                },
                **_CAPABILITIES_SCHEMA,
                **_LABELS_SCHEMA,
                **_NETWORK_CONFIG_SCHEMA,
            },
            **_check_not_both_fields_present('instance_tags', 'labels')
        },
        'gcp': {
            'type': 'object',
            'required': [],
            'additionalProperties': False,
            'properties': {
                'prioritize_reservations': {
                    'type': 'boolean',
                },
                'specific_reservations': {
                    'type': 'array',
                    'items': {
                        'type': 'string',
                    },
                },
                'managed_instance_group': {
                    'type': 'object',
                    'required': ['run_duration'],
                    'additionalProperties': False,
                    'properties': {
                        'run_duration': {
                            'type': 'integer',
                        },
                        'provision_timeout': {
                            'type': 'integer',
                        }
                    }
                },
                'force_enable_external_ips': {
                    'type': 'boolean'
                },
                'enable_gvnic': {
                    'type': 'boolean'
                },
                'enable_gpu_direct': {
                    'type': 'boolean'
                },
                'placement_policy': {
                    'type': 'string',
                },
                'vpc_name': {
                    'oneOf': [
                        {
                            'type': 'string',
                            # vpc-name or project-id/vpc-name
                            # VPC name and Project ID have -, a-z, and 0-9.
                            'pattern': '^(?:[-a-z0-9]+/)?[-a-z0-9]+$'
                        },
                        {
                            'type': 'null',
                        }
                    ],
                },
                **_CAPABILITIES_SCHEMA,
                **_LABELS_SCHEMA,
                **_NETWORK_CONFIG_SCHEMA,
            },
            **_check_not_both_fields_present('instance_tags', 'labels')
        },
        'azure': {
            'type': 'object',
            'required': [],
            'additionalProperties': False,
            'properties': {
                'storage_account': {
                    'type': 'string',
                },
                'resource_group_vm': {
                    'type': 'string',
                },
            }
        },
        'kubernetes': {
            'type': 'object',
            'required': [],
            'additionalProperties': False,
            'properties': {
                'allowed_contexts': {
                    'oneOf': [{
                        'type': 'array',
                        'items': {
                            'type': 'string',
                        },
                    }, {
                        'type': 'string',
                        'pattern': '^all$'
                    }]
                },
                'context_configs': {
                    'type': 'object',
                    'required': [],
                    'properties': {},
                    # Properties are kubernetes context names.
                    'additionalProperties': {
                        'type': 'object',
                        'required': [],
                        'additionalProperties': False,
                        'properties': {
                            **_CONTEXT_CONFIG_SCHEMA_KUBERNETES,
                        },
                    },
                },
                **_CONTEXT_CONFIG_SCHEMA_KUBERNETES,
            }
        },
        'ssh': {
            'type': 'object',
            'required': [],
            'additionalProperties': False,
            'properties': {
                'allowed_node_pools': {
                    'type': 'array',
                    'items': {
                        'type': 'string',
                    },
                },
                'context_configs': {
                    'type': 'object',
                    'required': [],
                    'properties': {},
                    # Properties are ssh cluster names, which are the
                    # kubernetes context names without `ssh-` prefix.
                    'additionalProperties': {
                        'type': 'object',
                        'required': [],
                        'additionalProperties': False,
                        'properties': {
                            **_CONTEXT_CONFIG_SCHEMA_MINIMAL,
                        },
                    },
                },
                **_CONTEXT_CONFIG_SCHEMA_MINIMAL,
            }
        },
        'slurm': {
            'type': 'object',
            'required': [],
            'additionalProperties': False,
            'properties': {
                'allowed_clusters': {
                    'oneOf': [{
                        'type': 'array',
                        'items': {
                            'type': 'string',
                        },
                    }, {
                        'type': 'string',
                        'pattern': '^all$'
                    }]
                },
                'provision_timeout': {
                    'type': 'integer',
                },
            }
        },
        'oci': {
            'type': 'object',
            'required': [],
            'properties': {
                'region_configs': {
                    'type': 'object',
                    'required': [],
                    'properties': {},
                    # Properties are either 'default' or a region name.
                    'additionalProperties': {
                        'type': 'object',
                        'required': [],
                        'additionalProperties': False,
                        'properties': {
                            'compartment_ocid': {
                                'type': 'string',
                            },
                            'image_tag_general': {
                                'type': 'string',
                            },
                            'image_tag_gpu': {
                                'type': 'string',
                            },
                            'vcn_ocid': {
                                'type': 'string',
                            },
                            'vcn_subnet': {
                                'type': 'string',
                            },
                        }
                    },
                }
            },
        },
        'vast': {
            'type': 'object',
            'required': [],
            'additionalProperties': False,
            'properties': {
                'datacenter_only': {
                    'type': 'boolean',
                },
                'create_instance_kwargs': {
                    'type': 'object',
                },
            }
        },
        'nebius': {
            'type': 'object',
            'required': [],
            'properties': {
                **_NETWORK_CONFIG_SCHEMA, 'use_static_ip_address': {
                    'type': 'boolean',
                },
                'tenant_id': {
                    'type': 'string',
                },
                'domain': {
                    'type': 'string',
                },
                'region_configs': {
                    'type': 'object',
                    'required': [],
                    'properties': {},
                    'additionalProperties': {
                        'type': 'object',
                        'required': [],
                        'additionalProperties': False,
                        'properties': {
                            'project_id': {
                                'type': 'string',
                            },
                            'fabric': {
                                'type': 'string',
                            },
                            'filesystems': {
                                'type': 'array',
                                'items': {
                                    'type': 'object',
                                    'additionalProperties': False,
                                    'properties': {
                                        'filesystem_id': {
                                            'type': 'string',
                                        },
                                        'attach_mode': {
                                            'type': 'string',
                                            'case_sensitive_enum': [
                                                'READ_WRITE', 'READ_ONLY'
                                            ]
                                        },
                                        'mount_path': {
                                            'type': 'string',
                                        }
                                    }
                                }
                            },
                        },
                    }
                }
            },
        }
    }

    admin_policy_schema = {
        'type': 'string',
        'anyOf': [
            {
                # Check regex to be a valid python module path
                'pattern': (r'^[a-zA-Z_][a-zA-Z0-9_]*'
                            r'(\.[a-zA-Z_][a-zA-Z0-9_]*)+$'),
            },
            {
                # Check for valid HTTP/HTTPS URL
                'pattern': r'^https?://.*$',
            }
        ]
    }

    allowed_clouds = {
        # A list of cloud names that are allowed to be used
        'type': 'array',
        'items': {
            'type': 'string',
            'case_insensitive_enum':
                (list(constants.ALL_CLOUDS) + ['cloudflare'])
        }
    }

    docker_configs = {
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            'run_options': {
                'anyOf': [{
                    'type': 'string',
                }, {
                    'type': 'array',
                    'items': {
                        'type': 'string',
                    }
                }]
            }
        }
    }
    gpu_configs = {
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            'disable_ecc': {
                'type': 'boolean',
            },
        }
    }

    daemon_config = {
        'type': 'object',
        'required': [],
        'properties': {
            'log_level': {
                'type': 'string',
                'case_insensitive_enum': ['DEBUG', 'INFO', 'WARNING'],
            },
        }
    }

    daemon_schema: Dict[str, Any] = {
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {}
    }

    for daemon in daemons.INTERNAL_REQUEST_DAEMONS:
        daemon_schema['properties'][daemon.id] = daemon_config

    api_server = {
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            'endpoint': {
                'type': 'string',
                # Apply validation for URL
                'pattern': r'^https?://.*$',
            },
            'service_account_token': {
                'anyOf': [
                    {
                        'type': 'string',
                        # Validate that token starts with sky_ prefix
                        'pattern': r'^sky_.+$',
                    },
                    {
                        'type': 'null',
                    }
                ]
            },
            'requests_retention_hours': {
                'type': 'integer',
            },
            'cluster_event_retention_hours': {
                'type': 'number',
            },
            'cluster_debug_event_retention_hours': {
                'type': 'number',
            },
            'cluster_terminal_event_retention_hours': {
                'type': 'number',
            },
        }
    }

    rbac_schema = {
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            'default_role': {
                'type': 'string',
                'case_insensitive_enum': ['admin', 'user']
            },
        },
    }

    workspace_schema = {'type': 'string'}

    allowed_workspace_cloud_names = list(constants.ALL_CLOUDS) + ['cloudflare']
    # Create pattern for not supported clouds, i.e.
    # all clouds except aws, gcp, kubernetes, ssh, nebius
    not_supported_clouds = [
        cloud for cloud in allowed_workspace_cloud_names
        if cloud.lower() not in ['aws', 'gcp', 'kubernetes', 'ssh', 'nebius']
    ]
    not_supported_cloud_regex = '|'.join(not_supported_clouds)
    workspaces_schema = {
        'type': 'object',
        'required': [],
        # each key is a workspace name
        'additionalProperties': {
            'type': 'object',
            'additionalProperties': False,
            'patternProperties': {
                # Pattern for clouds with no workspace-specific config -
                # only allow 'disabled' property.
                f'^({not_supported_cloud_regex})$': {
                    'type': 'object',
                    'additionalProperties': False,
                    'properties': {
                        'disabled': {
                            'type': 'boolean'
                        }
                    },
                },
            },
            'properties': {
                # Explicit definition for GCP allows both project_id and
                # disabled
                'private': {
                    'type': 'boolean',
                },
                'allowed_users': {
                    'type': 'array',
                    'items': {
                        'type': 'string',
                    },
                },
                'gcp': {
                    'type': 'object',
                    'properties': {
                        'project_id': {
                            'type': 'string'
                        },
                        'disabled': {
                            'type': 'boolean'
                        },
                        **_CAPABILITIES_SCHEMA,
                    },
                    'additionalProperties': False,
                },
                'aws': {
                    'type': 'object',
                    'properties': {
                        'profile': {
                            'type': 'string'
                        },
                        'disabled': {
                            'type': 'boolean'
                        },
                        **_CAPABILITIES_SCHEMA,
                    },
                    'additionalProperties': False,
                },
                'ssh': {
                    'type': 'object',
                    'required': [],
                    'properties': {
                        'allowed_node_pools': {
                            'type': 'array',
                            'items': {
                                'type': 'string',
                            },
                        },
                        'disabled': {
                            'type': 'boolean'
                        },
                    },
                    'additionalProperties': False,
                },
                'kubernetes': {
                    'type': 'object',
                    'required': [],
                    'properties': {
                        'allowed_contexts': {
                            'oneOf': [{
                                'type': 'array',
                                'items': {
                                    'type': 'string',
                                },
                            }, {
                                'type': 'string',
                                'pattern': '^all$'
                            }]
                        },
                        'disabled': {
                            'type': 'boolean'
                        },
                        'kueue': {
                            'type': 'object',
                            'required': [],
                            'additionalProperties': False,
                            'properties': {
                                'local_queue_name': {
                                    'type': 'string',
                                },
                            },
                        },
                        'context_configs': {
                            'type': 'object',
                            'required': [],
                            'properties': {},
                            # Properties are kubernetes context names.
                            'additionalProperties': {
                                'type': 'object',
                                'required': [],
                                'additionalProperties': False,
                                'properties': {
                                    'kueue': {
                                        'type': 'object',
                                        'required': [],
                                        'additionalProperties': False,
                                        'properties': {
                                            'local_queue_name': {
                                                'type': 'string',
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                    'additionalProperties': False,
                },
                'nebius': {
                    'type': 'object',
                    'required': [],
                    'properties': {
                        'credentials_file_path': {
                            'type': 'string',
                        },
                        'tenant_id': {
                            'type': 'string',
                        },
                        'domain': {
                            'type': 'string',
                        },
                        'disabled': {
                            'type': 'boolean'
                        },
                    },
                    'additionalProperties': False,
                },
            },
        },
    }

    provision_configs = {
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            'ssh_timeout': {
                'type': 'integer',
                'minimum': 1,
            },
            'install_conda': {
                'type': 'boolean',
            },
        }
    }

    logs_schema = {
        'type': 'object',
        'required': ['store'],
        'additionalProperties': False,
        'properties': {
            'store': {
                'type': 'string',
                'case_insensitive_enum': ['gcp', 'aws'],
            },
            'gcp': {
                'type': 'object',
                'properties': {
                    'project_id': {
                        'type': 'string',
                    },
                    'credentials_file': {
                        'type': 'string',
                    },
                    'additional_labels': {
                        'type': 'object',
                        'additionalProperties': {
                            'type': 'string',
                        },
                    },
                },
            },
            'aws': {
                'type': 'object',
                'properties': {
                    'region': {
                        'type': 'string',
                    },
                    'credentials_file': {
                        'type': 'string',
                    },
                    'log_group_name': {
                        'type': 'string',
                    },
                    'log_stream_prefix': {
                        'type': 'string',
                    },
                    'auto_create_group': {
                        'type': 'boolean',
                    },
                    'additional_tags': {
                        'type': 'object',
                        'additionalProperties': {
                            'type': 'string',
                        },
                    },
                },
            },
        },
    }

    for cloud, config in cloud_configs.items():
        if cloud == 'aws':
            config['properties'].update(
                {'remote_identity': _PROPERTY_NAME_OR_CLUSTER_NAME_TO_PROPERTY})
        elif cloud == 'kubernetes':
            config['properties'].update(_REMOTE_IDENTITY_SCHEMA_KUBERNETES)
        else:
            config['properties'].update(_REMOTE_IDENTITY_SCHEMA)

    # TODO (kyuds): deprecated; remove v0.13.0
    data_schema = {
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            'mount_cached': {
                'type': 'object',
                'required': [],
                'additionalProperties': False,
                'properties': {
                    'sequential_upload': {
                        'type': 'boolean',
                    },
                },
            },
        },
    }

    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            # TODO Replace this with whatever syang cooks up
            'workspace': {
                'type': 'string',
            },
            'db': {
                'type': 'string',
            },
            'jobs': _get_controller_schema(),
            'serve': _get_controller_schema(),
            'allowed_clouds': allowed_clouds,
            'admin_policy': admin_policy_schema,
            'docker': docker_configs,
            'nvidia_gpus': gpu_configs,
            'api_server': api_server,
            'active_workspace': workspace_schema,
            'workspaces': workspaces_schema,
            'provision': provision_configs,
            'rbac': rbac_schema,
            'logs': logs_schema,
            'daemons': daemon_schema,
            'data': data_schema,
            **cloud_configs,
        },
    }
