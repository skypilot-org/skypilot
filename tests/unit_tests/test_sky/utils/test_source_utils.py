"""Unit tests for sky.utils.source_utils."""
from sky.utils import source_utils


class TestMinifyPythonSource:
    """Strips docstrings/comments but preserves code."""

    def test_strips_module_and_function_docstrings(self):
        source = ('"""module doc"""\n'
                  'def foo():\n'
                  '    """fn doc"""\n'
                  '    return 1\n')
        out = source_utils.minify_python_source(source)
        assert 'module doc' not in out
        assert 'fn doc' not in out
        assert 'def foo' in out
        assert 'return 1' in out

    def test_strips_comments(self):
        source = 'x = 1  # trailing\n# leading\ny = 2\n'
        out = source_utils.minify_python_source(source)
        assert 'trailing' not in out
        assert 'leading' not in out
        assert 'x = 1' in out
        assert 'y = 2' in out

    def test_preserves_string_literals_that_are_not_docstrings(self):
        # Strings used as RHS or argument must survive — only "string
        # is its own statement at top of module/class/func" gets cut.
        source = 'def f():\n    msg = "hello"\n    return msg\n'
        out = source_utils.minify_python_source(source)
        assert '"hello"' in out

    def test_strips_class_docstring(self):
        source = 'class C:\n    """class doc"""\n    x = 1\n'
        out = source_utils.minify_python_source(source)
        assert 'class doc' not in out
        assert 'class C' in out
        assert 'x = 1' in out

    def test_output_round_trips_compile(self):
        # End-to-end on a non-trivial input: docstrings + comments +
        # multiple defs + non-docstring strings. The minified output
        # must still parse.
        source = ('"""mod"""\n'
                  'import os  # noqa\n'
                  '\n'
                  'def a():\n'
                  '    """a"""\n'
                  '    return "literal"\n'
                  '\n'
                  'class K:\n'
                  '    """k"""\n'
                  '    def m(self):\n'
                  '        return os.environ.get("X", "y")\n')
        out = source_utils.minify_python_source(source)
        compile(out, '<test>', 'exec')
