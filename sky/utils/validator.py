"""This module contains a custom validator for the JSON Schema specification.

The main motivation behind extending the existing JSON Schema validator is to
allow for case-insensitive enum matching since this is currently not supported
by the latest JSON Schema specification.
"""
import jsonschema


def case_insensitive_enum(validator, enums, instance, schema):  # pylint: disable=unused-argument
    if instance.lower() not in enums:
        yield jsonschema.ValidationError(
            f'{instance!r} is not one of {enums!r}')


SchemaValidator = jsonschema.validators.extend(
    jsonschema.Draft202012Validator,
    validators={'case_insensitive_enum': case_insensitive_enum})
