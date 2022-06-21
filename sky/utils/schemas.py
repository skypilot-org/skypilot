"""This module contains schemas used to validate objects.

Schemas conform to the JSON Schema specification as defined at
https://json-schema.org/
"""

from sky.clouds import cloud
from sky.data import storage


def get_resources_schema():
    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'type': 'object',
        'required': [],
        'additionalProperties': False,
        'properties': {
            'cloud': {
                'type': 'string',
                'enum': list(cloud.CLOUD_REGISTRY.keys()),
            },
            'region': {
                'type': 'string',
            },
            'accelerators': {
                'type': 'string',
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
            'accelerator_args': {
                'type': 'object',
                'required': [],
                'additionalProperties': False,
                'properties': {
                    'tf_version': {
                        'type': 'string',
                    },
                    'tpu_name': {
                        'type': 'string',
                    }
                }
            }
        }
    }


def get_storage_schema():
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
                'type': 'string',
            },
            'store': {
                'type': 'string',
                'enum': [type.value for type in storage.StoreType]
            },
            'persistent': {
                'type': 'boolean',
            },
            'mode': {
                'type': 'string',
                'enum': [mode.value for mode in storage.StorageMode]
            }
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
            'num_nodes': {
                'type': 'integer',
            },
            # Resources config is validated separately using RESOURCES_SCHEMA
            'resources': {
                'type': 'object',
            },
            # Storage config is validated separately using STORAGE_SCHEMA
            'file_mounts': {
                'type': 'object',
            },
            'setup': {
                'type': 'string',
            },
            'run': {
                'type': 'string',
            }
        }
    }
