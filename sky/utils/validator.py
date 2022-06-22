"""This module contains a custom validator for the JSON Schema specification.

The main motivation behind extending the existing JSON Schema validator is to
allow for case-insensitive enum matching since this is currently not supported
by the JSON Schema specification.
"""
import jsonschema


def case_insensitive_enum(validator, enums, instance, schema):
    del validator, schema  # Unused.
    if instance.lower() not in [enum.lower() for enum in enums]:
        yield jsonschema.ValidationError(
            f'{instance!r} is not one of {enums!r}')


SchemaValidator = jsonschema.validators.extend(
    jsonschema.Draft7Validator,
    validators={'case_insensitive_enum': case_insensitive_enum})
