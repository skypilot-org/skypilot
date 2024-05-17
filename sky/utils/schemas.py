"""This module contains schemas used to validate objects.

Schemas conform to the JSON Schema specification as defined at
https://json-schema.org/
"""
import enum


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


def _get_single_resources_schema():
    """Schema for a single resource in a resources list."""
    # To avoid circular imports, only import when needed.
    # pylint: disable=import-outside-toplevel
    from sky.clouds import service_catalog
    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            'cloud': {
                'type': 'string',
                'case_insensitive_enum': list(service_catalog.ALL_CLOUDS)
            },
            'region': {
                'type': 'string',
            },
            'zone': {
                'type': 'string',
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
            # Deprecated: use 'job_recovery' instead. This is for backward
            # compatibility, and can be removed in 0.8.0.
            'spot_recovery': {
                'type': 'string',
            },
            'job_recovery': {
                'type': 'string',
            },
            'disk_size': {
                'type': 'integer',
            },
            'disk_tier': {
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
                    }
                }
            },
            'image_id': {
                'anyOf': [{
                    'type': 'string',
                }, {
                    'type': 'object',
                    'required': [],
                }]
            },
            # The following fields are for internal use only.
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
        # Avoid job_recovery and spot_recovery being present at the same time.
        **_check_not_both_fields_present('job_recovery', 'spot_recovery')
    }


def get_storage_schema():
    # pylint: disable=import-outside-toplevel
    from sky.data import storage
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
            '_force_delete': {
                'type': 'boolean',
            }
        }
    }


def get_service_schema():
    """Schema for top-level `service:` field (for SkyServe)."""
    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'type': 'object',
        'required': ['readiness_probe'],
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
                        'post_data': {
                            'anyOf': [{
                                'type': 'string',
                            }, {
                                'type': 'object',
                            }]
                        }
                    }
                }]
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
                    'target_qps_per_replica': {
                        'type': 'number',
                        'minimum': 0,
                    },
                    'dynamic_ondemand_fallback': {
                        'type': 'boolean',
                    },
                    'base_ondemand_fallback_replicas': {
                        'type': 'integer',
                        'minimum': 0,
                    },
                    'upscale_delay_seconds': {
                        'type': 'number',
                    },
                    'downscale_delay_seconds': {
                        'type': 'number',
                    },
                    # TODO(MaoZiming): Fields `qps_upper_threshold`,
                    # `qps_lower_threshold` and `auto_restart` are deprecated.
                    # Temporarily keep these fields for backward compatibility.
                    # Remove after 2 minor release, i.e., 0.6.0.
                    'auto_restart': {
                        'type': 'boolean',
                    },
                    'qps_upper_threshold': {
                        'type': 'number',
                    },
                    'qps_lower_threshold': {
                        'type': 'number',
                    },
                }
            },
            'replicas': {
                'type': 'integer',
            },
        }
    }


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
                'type': 'string',
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
    'vpc_name': {
        'oneOf': [{
            'type': 'string',
        }, {
            'type': 'null',
        }],
    },
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
    # Deprecated: 'instance_tags' is replaced by 'labels'. Keeping for backward
    # compatibility. Will be removed after 0.7.0.
    'instance_tags': {
        'type': 'object',
        'required': [],
        'additionalProperties': {
            'type': 'string',
        },
    },
    'labels': {
        'type': 'object',
        'required': [],
        'additionalProperties': {
            'type': 'string',
        },
    }
}


class RemoteIdentityOptions(enum.Enum):
    """Enum for remote identity types.

    Some clouds (e.g., AWS, Kubernetes) also allow string values for remote
    identity, which map to the service account/role to use. Those are not
    included in this enum.
    """
    LOCAL_CREDENTIALS = 'LOCAL_CREDENTIALS'
    SERVICE_ACCOUNT = 'SERVICE_ACCOUNT'


def get_default_remote_identity(cloud: str) -> str:
    """Get the default remote identity for the specified cloud."""
    if cloud == 'kubernetes':
        return RemoteIdentityOptions.SERVICE_ACCOUNT.value
    return RemoteIdentityOptions.LOCAL_CREDENTIALS.value


_REMOTE_IDENTITY_SCHEMA = {
    'remote_identity': {
        'type': 'string',
        'case_insensitive_enum': [
            option.value for option in RemoteIdentityOptions
        ]
    }
}

_REMOTE_IDENTITY_SCHEMA_AWS = {
    'remote_identity': {
        'oneOf': [
            {
                'type': 'string'
            },
            {
                # A list of single-element dict to pretain the order.
                # Example:
                #  remote_identity:
                #    - my-cluster1-*: my-iam-role-1
                #    - my-cluster2-*: my-iam-role-2
                #    - "*"": my-iam-role-3
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
}

_REMOTE_IDENTITY_SCHEMA_KUBERNETES = {
    'remote_identity': {
        'type': 'string'
    },
}


def get_config_schema():
    # pylint: disable=import-outside-toplevel
    from sky.clouds import service_catalog
    from sky.utils import kubernetes_enums

    resources_schema = {
        k: v
        for k, v in get_resources_schema().items()
        # Validation may fail if $schema is included.
        if k != '$schema'
    }
    resources_schema['properties'].pop('ports')
    controller_resources_schema = {
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
                }
            },
        }
    }
    cloud_configs = {
        'aws': {
            'type': 'object',
            'required': [],
            'additionalProperties': False,
            'properties': {
                'security_group_name': {
                    'type': 'string'
                },
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
                **_LABELS_SCHEMA,
                **_NETWORK_CONFIG_SCHEMA,
            },
            **_check_not_both_fields_present('instance_tags', 'labels')
        },
        'kubernetes': {
            'type': 'object',
            'required': [],
            'additionalProperties': False,
            'properties': {
                'networking': {
                    'type': 'string',
                    'case_insensitive_enum': [
                        type.value
                        for type in kubernetes_enums.KubernetesNetworkingMode
                    ]
                },
                'ports': {
                    'type': 'string',
                    'case_insensitive_enum': [
                        type.value
                        for type in kubernetes_enums.KubernetesPortMode
                    ]
                },
                'pod_config': {
                    'type': 'object',
                    'required': [],
                    # Allow arbitrary keys since validating pod spec is hard
                    'additionalProperties': True,
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
                    }
                },
                'provision_timeout': {
                    'type': 'integer',
                },
                'autoscaler': {
                    'type': 'string',
                    'case_insensitive_enum': [
                        type.value
                        for type in kubernetes_enums.KubernetesAutoscalerType
                    ]
                },
            }
        },
        'oci': {
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
                    'vcn_subnet': {
                        'type': 'string',
                    },
                }
            },
        },
    }

    allowed_clouds = {
        # A list of cloud names that are allowed to be used
        'type': 'array',
        'items': {
            'type': 'string',
            'case_insensitive_enum':
                (list(service_catalog.ALL_CLOUDS) + ['cloudflare'])
        }
    }

    for cloud, config in cloud_configs.items():
        if cloud == 'aws':
            config['properties'].update(_REMOTE_IDENTITY_SCHEMA_AWS)
        elif cloud == 'kubernetes':
            config['properties'].update(_REMOTE_IDENTITY_SCHEMA_KUBERNETES)
        else:
            config['properties'].update(_REMOTE_IDENTITY_SCHEMA)
    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            'jobs': controller_resources_schema,
            'spot': controller_resources_schema,
            'serve': controller_resources_schema,
            'allowed_clouds': allowed_clouds,
            **cloud_configs,
        },
        # Avoid spot and jobs being present at the same time.
        **_check_not_both_fields_present('spot', 'jobs')
    }
