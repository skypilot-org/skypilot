"""Tests for Sky Batch remote function validation.

This module tests the closure and global reference validation that ensures
remote functions don't reference external variables that won't be available
on workers.
"""
import pytest

import sky.batch


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
    """Test detection of module-level global references.

    Note: These tests are limited because globals defined at module level
    in this test file become closures when used in nested functions.
    The real-world case (imports at module level) is tested in integration
    tests or by running actual batch jobs.
    """

    def test_validation_framework_exists(self):
        """Test that global validation function exists."""
        from sky.batch.remote import _validate_no_global_references

        # Just verify the function exists and is callable
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
