"""Tests for Sky Batch function serialization.

This module tests the source-code-based function serialization that is
Python-version-agnostic (works across Python 3.8, 3.9, 3.10, 3.11, etc.).
"""
import pytest

from sky.batch import utils


def sample_batch_function():
    """Sample function that would be used with Sky Batch."""
    import sky.batch

    for batch in sky.batch.load():
        # Process each item in the batch
        results = [{'output': item['text'] * 2} for item in batch]
        sky.batch.save_results(results)


def sample_with_helper():
    """Sample function with internal helper."""
    import sky.batch

    def helper(x):
        return x * 2

    for batch in sky.batch.load():
        results = [{'value': helper(item['x'])} for item in batch]
        sky.batch.save_results(results)


class TestFunctionSerialization:
    """Test source-based function serialization."""

    def test_basic_serialization(self):
        """Test that a basic function can be serialized and deserialized."""
        serialized = utils.serialize_function(sample_batch_function)

        assert isinstance(serialized, str)
        assert len(serialized) > 0

    def test_deserialization(self):
        """Test that a function can be deserialized correctly."""
        serialized = utils.serialize_function(sample_batch_function)
        deserialized = utils.deserialize_function(serialized)

        assert callable(deserialized)
        assert deserialized.__name__ == sample_batch_function.__name__

    def test_docstring_preserved(self):
        """Test that function docstring is preserved."""
        serialized = utils.serialize_function(sample_batch_function)
        deserialized = utils.deserialize_function(serialized)

        assert deserialized.__doc__ == sample_batch_function.__doc__

    def test_function_with_helper(self):
        """Test serialization of function with internal helper."""
        serialized = utils.serialize_function(sample_with_helper)
        deserialized = utils.deserialize_function(serialized)

        assert callable(deserialized)
        assert deserialized.__name__ == sample_with_helper.__name__

    def test_serialization_format(self):
        """Test that serialization produces correct format."""
        import base64
        import json

        serialized = utils.serialize_function(sample_batch_function)

        # Decode and verify structure
        decoded = base64.b64decode(serialized.encode('utf-8'))
        payload = json.loads(decoded)

        assert payload['type'] == 'source'
        assert 'source' in payload
        assert 'name' in payload
        assert payload['name'] == 'sample_batch_function'
        assert 'def sample_batch_function' in payload['source']

    def test_roundtrip_preservation(self):
        """Test that serialize -> deserialize preserves function."""
        # Can't easily test execution without full Sky Batch setup,
        # but we can verify the function structure is preserved
        serialized = utils.serialize_function(sample_batch_function)
        deserialized = utils.deserialize_function(serialized)

        # Check that key attributes are preserved
        assert deserialized.__name__ == sample_batch_function.__name__
        assert deserialized.__doc__ == sample_batch_function.__doc__

        # Check that the deserialized function has the same code structure
        # (both should reference 'sky', 'batch', 'load', 'save_results')
        orig_names = set(sample_batch_function.__code__.co_names)
        new_names = set(deserialized.__code__.co_names)

        # Key names should be present in both
        key_names = {'sky.batch', 'batch', 'load', 'save_results'}
        assert key_names.issubset(orig_names)
        assert key_names.issubset(new_names)


class TestSerializationErrorHandling:
    """Test error handling in serialization."""

    def test_cannot_serialize_builtin(self):
        """Test that built-in functions raise appropriate error."""
        with pytest.raises(TypeError, match='unable to retrieve source code'):
            utils.serialize_function(len)

    def test_cannot_serialize_lambda(self):
        """Test that dynamically-created functions raise appropriate error."""
        # Lambdas defined in source files CAN be serialized via
        # inspect.getsource(), so we use exec() to create one without
        # a backing source file.
        ns = {}
        exec('fn = lambda x: x * 2', ns)  # pylint: disable=exec-used
        dynamic_fn = ns['fn']

        with pytest.raises(TypeError, match='unable to retrieve source code'):
            utils.serialize_function(dynamic_fn)

    def test_invalid_deserialization(self):
        """Test that invalid serialized data raises error."""
        import base64
        import json

        # Create invalid payload
        invalid_payload = {
            'type': 'unknown_type',
            'data': 'invalid',
        }
        serialized = base64.b64encode(
            json.dumps(invalid_payload).encode('utf-8')).decode('utf-8')

        with pytest.raises(ValueError,
                           match='Unknown or missing serialization type'):
            utils.deserialize_function(serialized)

    def test_deserialization_with_syntax_error(self):
        """Test that syntactically invalid source raises error."""
        import base64
        import json

        # Create payload with invalid Python source
        invalid_payload = {
            'type': 'source',
            'source': 'def bad_func(: pass',  # Syntax error
            'name': 'bad_func',
            'version': '1.0',
        }
        serialized = base64.b64encode(
            json.dumps(invalid_payload).encode('utf-8')).decode('utf-8')

        with pytest.raises(ValueError,
                           match='Failed to execute function source'):
            utils.deserialize_function(serialized)


class TestCrossVersionCompatibility:
    """Test that serialization is Python-version-agnostic."""

    def test_no_bytecode_in_payload(self):
        """Verify that serialized format doesn't contain bytecode."""
        import base64
        import json

        serialized = utils.serialize_function(sample_batch_function)
        decoded = base64.b64decode(serialized.encode('utf-8'))
        payload = json.loads(decoded)

        # Should be JSON (text-based), not binary bytecode
        assert isinstance(payload, dict)
        assert isinstance(payload['source'], str)

        # Should not contain bytecode indicators
        assert 'pickle' not in payload['type'].lower()
        assert payload['type'] == 'source'

    def test_human_readable_source(self):
        """Test that serialized source is human-readable."""
        import base64
        import json

        serialized = utils.serialize_function(sample_batch_function)
        decoded = base64.b64decode(serialized.encode('utf-8'))
        payload = json.loads(decoded)

        source = payload['source']

        # Should be readable Python source code
        assert 'def sample_batch_function' in source
        assert 'sky.batch.load()' in source
        assert 'sky.batch.save_results' in source

        # Should not have binary/bytecode content
        assert not any(c < ' ' and c not in '\n\t' for c in source)
