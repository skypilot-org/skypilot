"""This module contains schemas used to validate objects.

Schemas conform to the JSON Schema specification as defined at https://json-schema.org/
"""

_RESOURCES_DEF = {
    "type": "object",
    "required": [],
    "additionalProperties": False,
    "properties": {
        "cloud": {
            "type": "string",
            "enum": ["aws", "azure", "gcp"]
        },
        "region": {
            "type": "string",
        },
        "accelerators": {
            "type": "string",
        },
        "instance_type": {
            "type": "string",
        },
        "use_spot": {
            "type": "boolean",
        },
        "spot_recovery": {
            "type": "string",
        },
        "disk_size": {
            "type": "integer",
        },
        "accelerator_args": {
            "type": "object",
            "required": [],
            "additionalProperties": False,
            "properties": {
                "tf_version": {
                    "type": "string",
                },
                "tpu_name": {
                    "type": "string",
                }
            }
        }
    }
}

_STORAGE_DEF = {
    "oneOf": [{
        "type": "object",
        "required": [],
        "additionalProperties": False,
        "properties": {
            "name": {
                "type": "string",
            },
            "source": {
                "type": "string",
            },
            "store": {
                "type": "string",
                "enum": ["s3", "gcs"]
            },
            "persistent": {
                "type": "boolean",
            },
            "mode": {
                "type": "string",
                "enum": ["MOUNT", "COPY"]
            }
        }
    }, {
        "type": "string"
    }]
}

TASK_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [],
    "additionalProperties": False,
    "properties": {
        "name": {
            "type": "string",
        },
        "workdir": {
            "type": "string",
        },
        "num_nodes": {
            "type": "integer",
        },
        "resources": {
            "$ref": "#/$defs/resources",
        },
        "file_mounts": {
            "type": "object",
            "required": [],
            "additionalProperties": {
                "$ref": "#/$defs/storage"
            }
        },
        "setup": {
            "type": "string",
        },
        "run": {
            "type": "string",
        }
    },
    "$defs": {
        "storage": _STORAGE_DEF,
        "resources": _RESOURCES_DEF
    }
}
