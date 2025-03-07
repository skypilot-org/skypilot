"""This module contains a custom validator for the JSON Schema specification.

The main motivation behind extending the existing JSON Schema validator is to
allow for case-insensitive enum matching since this is currently not supported
by the JSON Schema specification.
"""
import typing

from sky.adaptors import common as adaptors_common

if typing.TYPE_CHECKING:
    import jsonschema
else:
    jsonschema = adaptors_common.LazyImport('jsonschema')


def case_insensitive_enum(validator, enums, instance, schema):
    del validator, schema  # Unused.
    if instance.lower() not in [enum.lower() for enum in enums]:
        yield jsonschema.ValidationError(
            f'{instance!r} is not one of {enums!r}')


# Move this to a function to delay initialization
def get_schema_validator():
    """Get the schema validator class, initializing it only when needed."""
    return jsonschema.validators.extend(
        jsonschema.Draft7Validator,
        validators={'case_insensitive_enum': case_insensitive_enum})
