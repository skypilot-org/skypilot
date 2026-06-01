"""Tests for Sky Batch.

Covers function serialization, remote function validation, and utility
functions.
"""
import base64
import json
import os
import tempfile
from unittest.mock import patch

import pytest

import sky.batch
from sky.batch import utils

# ---------------------------------------------------------------------------
# Sample functions used by serialization tests
# ---------------------------------------------------------------------------


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


# ===========================================================================
# Serialization
# ===========================================================================


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


# ===========================================================================
# Remote function validation
# ===========================================================================


class TestValidFunctions:
    """Test that valid remote functions pass validation."""

    def test_function_with_no_external_references(self):
        """Test function with no external dependencies."""

        @sky.batch.remote_function
        def valid():
            for batch in sky.batch.load():
                results = [{'output': item['text'] * 2} for item in batch]
                sky.batch.save_results(results)

        assert sky.batch.remote.is_remote_function(valid)

    def test_function_with_internal_imports(self):
        """Test function that imports modules inside."""

        @sky.batch.remote_function
        def valid():
            import json

            for batch in sky.batch.load():
                results = [json.loads(item['data']) for item in batch]
                sky.batch.save_results(results)

        assert sky.batch.remote.is_remote_function(valid)

    def test_function_with_internal_helper(self):
        """Test function with helper defined inside."""

        @sky.batch.remote_function
        def valid():

            def helper(x):
                return x * 2

            for batch in sky.batch.load():
                results = [{'value': helper(item['x'])} for item in batch]
                sky.batch.save_results(results)

        assert sky.batch.remote.is_remote_function(valid)

    def test_function_loading_model_internally(self):
        """Test recommended pattern of loading models inside function."""

        @sky.batch.remote_function
        def valid():
            # Load model inside (runs once per worker)
            def load_model():
                return {'type': 'dummy'}

            model = load_model()

            for batch in sky.batch.load():
                results = [{'pred': f"model_{model['type']}"} for item in batch]
                sky.batch.save_results(results)

        assert sky.batch.remote.is_remote_function(valid)

    def test_function_with_literals(self):
        """Test that literal constants are allowed."""

        @sky.batch.remote_function
        def valid():
            for batch in sky.batch.load():
                results = [{
                    'value': item['x'] * 2
                } for item in batch]  # 2 is a literal
                sky.batch.save_results(results)

        assert sky.batch.remote.is_remote_function(valid)


class TestClosureDetection:
    """Test detection of closures over external variables."""

    def test_closure_over_variable(self):
        """Test that closures over variables are rejected."""
        external_var = 42

        with pytest.raises(ValueError,
                           match='references external variables.*external_var'):

            @sky.batch.remote_function
            def invalid():
                for batch in sky.batch.load():
                    results = [{
                        'value': item['x'] * external_var
                    } for item in batch]
                    sky.batch.save_results(results)

    def test_closure_over_function(self):
        """Test that closures over functions are rejected."""

        def helper(x):
            return x * 2

        with pytest.raises(ValueError,
                           match='references external variables.*helper'):

            @sky.batch.remote_function
            def invalid():
                for batch in sky.batch.load():
                    results = [{'value': helper(item['x'])} for item in batch]
                    sky.batch.save_results(results)

    def test_closure_over_module(self):
        """Test that closures over imported modules are rejected."""
        import numpy as np

        with pytest.raises(ValueError,
                           match='references external variables.*np'):

            @sky.batch.remote_function
            def invalid():
                for batch in sky.batch.load():
                    results = [{'mean': np.mean([item['x']])} for item in batch]
                    sky.batch.save_results(results)

    def test_multiple_closures(self):
        """Test that multiple closures are all reported."""
        var1 = 1
        var2 = 2

        with pytest.raises(ValueError,
                           match='references external variables.*(var1|var2)'):

            @sky.batch.remote_function
            def invalid():
                for batch in sky.batch.load():
                    results = [{
                        'value': item['x'] * var1 + var2
                    } for item in batch]
                    sky.batch.save_results(results)


class TestErrorMessages:
    """Test that error messages are helpful and actionable."""

    def test_closure_error_message_suggests_fix(self):
        """Test that closure errors suggest how to fix the issue."""
        model = {'type': 'dummy'}

        with pytest.raises(ValueError) as exc_info:

            @sky.batch.remote_function
            def invalid():
                for batch in sky.batch.load():
                    results = [{
                        'pred': model
                    } for item in batch]  # Uses 'model'
                    sky.batch.save_results(results)

        error_msg = str(exc_info.value)

        # Check error message content
        assert 'model' in error_msg
        assert 'external variables' in error_msg.lower()
        assert 'clean namespace' in error_msg.lower()

        # Check that it suggests a fix
        assert 'To fix' in error_msg or 'Move' in error_msg

    def test_error_shows_function_name(self):
        """Test that error includes the function name."""
        x = 42

        with pytest.raises(ValueError) as exc_info:

            @sky.batch.remote_function
            def my_special_function():
                return x

        error_msg = str(exc_info.value)
        assert 'my_special_function' in error_msg


class TestGlobalReferenceDetection:
    """Test detection of module-level global references."""

    def test_validation_framework_exists(self):
        """Test that global validation function exists."""
        from sky.batch.remote import _validate_no_global_references

        assert callable(_validate_no_global_references)

    def test_ast_helper_functions_exist(self):
        """Test that AST helper functions exist."""
        from sky.batch.remote import _collect_local_names
        from sky.batch.remote import _collect_referenced_names

        assert callable(_collect_local_names)
        assert callable(_collect_referenced_names)


class TestDecorator:
    """Test the @remote_function decorator behavior."""

    def test_decorator_marks_function(self):
        """Test that decorator marks function as remote."""

        @sky.batch.remote_function
        def func():
            pass

        assert hasattr(func, '_is_remote_function')
        assert func._is_remote_function is True

    def test_is_remote_function_helper(self):
        """Test the is_remote_function helper."""

        @sky.batch.remote_function
        def remote_func():
            pass

        def regular_func():
            pass

        assert sky.batch.remote.is_remote_function(remote_func)
        assert not sky.batch.remote.is_remote_function(regular_func)

    def test_decorator_returns_same_function(self):
        """Test that decorator doesn't wrap the function."""

        def original():
            """Original docstring."""
            pass

        decorated = sky.batch.remote_function(original)

        # Should be the same function object (just with metadata added)
        assert decorated is original
        assert decorated.__name__ == 'original'
        assert decorated.__doc__ == 'Original docstring.'


class TestEdgeCases:
    """Test edge cases in validation."""

    def test_function_with_no_body(self):
        """Test that function with just pass is valid."""

        @sky.batch.remote_function
        def valid():
            pass

        assert sky.batch.remote.is_remote_function(valid)

    def test_function_with_class_definition(self):
        """Test function that defines a class inside."""

        @sky.batch.remote_function
        def valid():

            class Helper:

                def process(self, x):
                    return x * 2

            helper = Helper()
            for batch in sky.batch.load():
                results = [{
                    'value': helper.process(item['x'])
                } for item in batch]
                sky.batch.save_results(results)

        assert sky.batch.remote.is_remote_function(valid)

    def test_function_with_nested_function(self):
        """Test function with nested function definition."""

        @sky.batch.remote_function
        def valid():

            def outer():

                def inner(x):
                    return x * 2

                return inner

            helper = outer()
            for batch in sky.batch.load():
                results = [{'value': helper(item['x'])} for item in batch]
                sky.batch.save_results(results)

        assert sky.batch.remote.is_remote_function(valid)

    def test_function_with_exception_handling(self):
        """Test function with try/except blocks."""

        @sky.batch.remote_function
        def valid():
            for batch in sky.batch.load():
                results = []
                for item in batch:
                    try:
                        value = int(item['x']) * 2
                    except (ValueError, KeyError) as e:
                        value = 0
                    results.append({'value': value})
                sky.batch.save_results(results)

        assert sky.batch.remote.is_remote_function(valid)


# ===========================================================================
# Utility functions
# ===========================================================================


class TestCountJsonlLinesFromCloud:
    """Tests for count_jsonl_lines_from_cloud."""

    def _write_temp_jsonl(self, lines):
        """Helper: write lines to a temp file, return path."""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False)
        f.write('\n'.join(lines))
        if lines:
            f.write('\n')
        f.close()
        return f.name

    def _mock_download(self, src_path):
        """Return a mock download_from_cloud that copies a local file."""

        def _download(path, dest):
            del path  # unused
            with open(src_path, 'r') as src, open(dest, 'w') as dst:
                dst.write(src.read())

        return _download

    def test_basic(self):
        src = self._write_temp_jsonl([
            '{"a": 1}',
            '{"a": 2}',
            '{"a": 3}',
        ])
        try:
            with patch.object(utils,
                              'download_from_cloud',
                              side_effect=self._mock_download(src)):
                assert utils.count_jsonl_lines_from_cloud('s3://b/f.jsonl') == 3
        finally:
            os.remove(src)

    def test_single_line(self):
        src = self._write_temp_jsonl(['{"x": 1}'])
        try:
            with patch.object(utils,
                              'download_from_cloud',
                              side_effect=self._mock_download(src)):
                assert utils.count_jsonl_lines_from_cloud('s3://b/f.jsonl') == 1
        finally:
            os.remove(src)

    def test_empty_file(self):
        src = self._write_temp_jsonl([])
        try:
            with patch.object(utils,
                              'download_from_cloud',
                              side_effect=self._mock_download(src)):
                assert utils.count_jsonl_lines_from_cloud('s3://b/f.jsonl') == 0
        finally:
            os.remove(src)

    def test_large_count(self):
        n = 10_000
        src = self._write_temp_jsonl([f'{{"i": {i}}}' for i in range(n)])
        try:
            with patch.object(utils,
                              'download_from_cloud',
                              side_effect=self._mock_download(src)):
                assert utils.count_jsonl_lines_from_cloud('s3://b/f.jsonl') == n
        finally:
            os.remove(src)
