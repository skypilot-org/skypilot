"""This module contains schemas used to validate objects.

Schemas conform to the JSON Schema specification as defined at
https://json-schema.org/
"""


def get_single_resources_schema():
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
            'spot_recovery': {
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
            }
        }
    }


def get_resources_schema():
    # To avoid circular imports, only import when needed.
    # pylint: disable=import-outside-toplevel
    from sky.clouds import service_catalog
    return {
        '$schema': 'http://json-schema.org/draft-07/schema#',
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
                # {'V100:1', 'A100:1'} will be
                # read as a string and converted to dict.
                'anyOf': [{
                    'type': 'string',
                }, {
                    'type': 'object',
                    'required': [],
                    'maxProperties': 1,
                    'additionalProperties': {
                        'type': 'number'
                    }
                }, {
                    'type': 'array',
                    'items': {
                        'type': 'string',
                    }
                }]
            },
            'instance_type': {
                'type': 'string',
            },
            'use_spot': {
                'type': 'boolean',
            },
            'spot_recovery': {
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
            'any_of': {
                'type': 'array',
                'items': {
                    k: v
                    for k, v in get_single_resources_schema().items()
                    # Validation may fail if $schema is included.
                    if k != '$schema'
                },
            },
            'ordered': {
                'type': 'array',
                'items': {
                    k: v
                    for k, v in get_single_resources_schema().items()
                    # Validation may fail if $schema is included.
                    if k != '$schema'
                },
            }
        }
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
                    },
                    'max_replicas': {
                        'type': 'integer',
                    },
                    'qps_upper_threshold': {
                        'type': 'number',
                    },
                    'qps_lower_threshold': {
                        'type': 'number',
                    },
                    'auto_restart': {
                        'type': 'boolean',
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
                        'type': 'string'
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


def get_config_schema():
    # pylint: disable=import-outside-toplevel
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

    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            'spot': controller_resources_schema,
            'serve': controller_resources_schema,
            'aws': {
                'type': 'object',
                'required': [],
                'additionalProperties': False,
                'properties': {
                    'instance_tags': {
                        'type': 'object',
                        'required': [],
                        'additionalProperties': {
                            'type': 'string',
                        },
                    },
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
            },
            'gcp': {
                'type': 'object',
                'required': [],
                'additionalProperties': False,
                'properties': {
                    'specific_reservations': {
                        'type': 'array',
                        'items': {
                            'type': 'string',
                        },
                        'minItems': 1,
                        'maxItems': 1,
                    },
                    'vpc_name': {
                        'oneOf': [{
                            'type': 'string',
                        }, {
                            'type': 'null',
                        }],
                    },
                }
            },
            'kubernetes': {
                'type': 'object',
                'required': [],
                'additionalProperties': False,
                'properties': {
                    'networking': {
                        'type': 'string',
                        'case_insensitive_enum': [
                            type.value for type in
                            kubernetes_enums.KubernetesNetworkingMode
                        ]
                    },
                }
            },
            'oci': {
                'type': 'object',
                'required': [],
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
    }
