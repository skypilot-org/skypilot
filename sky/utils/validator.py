"""This module contains a custom validator for the JSON Schema specification.

The main motivation behind extending the existing JSON Schema validator is to
allow for case-insensitive enum matching since this is currently not supported
by the JSON Schema specification.
"""
import jsonschema


def case_insensitive_enum(validator, enums, instance, schema):
    del validator, schema  # Unused.
    # Skip validation if instance is not a string (e.g., None)
    # This allows anyOf with null type to work properly
    if not isinstance(instance, str):
        yield jsonschema.ValidationError(f'{instance!r} is not a string')
        return
    if instance.lower() not in [enum.lower() for enum in enums]:
        yield jsonschema.ValidationError(
            f'{instance!r} is not one of {enums!r}')


def case_sensitive_enum(validator, enums, instance, schema):
    del validator, schema  # Unused.
    # Skip validation if instance is not a string (e.g., None)
    # This allows anyOf with null type to work properly
    if not isinstance(instance, str):
        yield jsonschema.ValidationError(f'{instance!r} is not a string')
        return
    if instance not in enums:
        yield jsonschema.ValidationError(
            f'{instance!r} is not one of {enums!r}')


# Move this to a function to delay initialization
def get_schema_validator():
    """Get the schema validator class, initializing it only when needed."""
    return jsonschema.validators.extend(
        jsonschema.Draft7Validator,
        validators={
            'case_insensitive_enum': case_insensitive_enum,
            'case_sensitive_enum': case_sensitive_enum
        })
