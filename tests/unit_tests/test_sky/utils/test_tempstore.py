"""Unit tests for sky.utils.tempstore module."""

import os
import pathlib
import tempfile
from unittest import mock

import pytest

from sky.utils import tempstore


def test_tempdir_context_manager():
    """Test tempdir context manager creates and cleans up temp directory."""
    temp_path = None

    with tempstore.tempdir() as temp_dir:
        temp_path = pathlib.Path(temp_dir)
        assert temp_path.exists()
        assert temp_path.is_dir()
        assert temp_path.name.startswith('sky-tmp')

    # Directory should be cleaned up after context exits
    assert not temp_path.exists()


def test_tempdir_context_isolation():
    """Test tempdir context manager provides isolated temp directories."""
    path1 = None
    path2 = None

    with tempstore.tempdir() as temp_dir1:
        path1 = temp_dir1
        with tempstore.tempdir() as temp_dir2:
            path2 = temp_dir2
            # Should be different directories
            assert path1 != path2
            assert pathlib.Path(path1).exists()
            assert pathlib.Path(path2).exists()

        # Inner temp dir should be cleaned up
        assert not pathlib.Path(path2).exists()
        # Outer temp dir should still exist
        assert pathlib.Path(path1).exists()

    # Both should be cleaned up now
    assert not pathlib.Path(path1).exists()
    assert not pathlib.Path(path2).exists()


def test_tempdir_nested_contexts():
    """Test nested tempdir contexts work correctly."""
    outer_dir = None
    inner_dir = None

    with tempstore.tempdir() as temp_dir1:
        outer_dir = temp_dir1
        # Create a temp dir inside the context
        inner_temp = tempstore.mkdtemp()
        assert inner_temp.startswith(outer_dir)

        with tempstore.tempdir() as temp_dir2:
            inner_dir = temp_dir2
            # This should create temp dir in the new context
            nested_temp = tempstore.mkdtemp()
            assert nested_temp.startswith(inner_dir)
            assert not nested_temp.startswith(outer_dir)

        # Back to outer context
        outer_temp = tempstore.mkdtemp()
        assert outer_temp.startswith(outer_dir)


def test_mkdtemp_no_context():
    """Test mkdtemp without tempdir context."""
    # Should work like regular tempfile.mkdtemp
    temp_dir = tempstore.mkdtemp()
    try:
        assert os.path.exists(temp_dir)
        assert os.path.isdir(temp_dir)
        # Should not have sky-tmp prefix since no context
        assert not os.path.basename(temp_dir).startswith('sky-tmp')
    finally:
        # Manual cleanup since no context
        if os.path.exists(temp_dir):
            os.rmdir(temp_dir)


def test_mkdtemp_with_context():
    """Test mkdtemp with tempdir context."""
    with tempstore.tempdir() as context_dir:
        temp_dir = tempstore.mkdtemp()

        # Should be created inside context directory
        assert temp_dir.startswith(context_dir)
        assert os.path.exists(temp_dir)
        assert os.path.isdir(temp_dir)

        # Should be automatically cleaned up when context exits


def test_mkdtemp_with_parameters():
    """Test mkdtemp with suffix, prefix, and dir parameters."""
    with tempstore.tempdir() as context_dir:
        # Test with suffix and prefix
        temp_dir = tempstore.mkdtemp(suffix='_test', prefix='test_')
        assert os.path.basename(temp_dir).startswith('test_')
        assert os.path.basename(temp_dir).endswith('_test')
        assert temp_dir.startswith(context_dir)

        # Test with explicit dir parameter
        subdir = 'subdir'
        temp_dir2 = tempstore.mkdtemp(dir=subdir)
        expected_parent = os.path.join(context_dir, subdir)
        assert temp_dir2.startswith(expected_parent)


def test_mkdtemp_explicit_dir_no_context():
    """Test mkdtemp with explicit dir but no context."""
    with tempfile.TemporaryDirectory() as temp_base:
        subdir = 'testsubdir'
        full_subdir = os.path.join(temp_base, subdir)
        os.makedirs(full_subdir)

        temp_dir = tempstore.mkdtemp(dir=full_subdir)
        try:
            assert temp_dir.startswith(full_subdir)
            assert os.path.exists(temp_dir)
        finally:
            if os.path.exists(temp_dir):
                os.rmdir(temp_dir)


def test_mkdtemp_explicit_dir_with_context():
    """Test mkdtemp with explicit dir and context."""
    with tempstore.tempdir() as context_dir:
        subdir = 'mysubdir'
        temp_dir = tempstore.mkdtemp(dir=subdir)

        # Should be created in context_dir/subdir
        expected_parent = os.path.join(context_dir, subdir)
        assert temp_dir.startswith(expected_parent)
        assert os.path.exists(temp_dir)

        # Test nested directory creation
        nested_dir = 'level1/level2/level3'
        nested_temp = tempstore.mkdtemp(dir=nested_dir)
        expected_nested = os.path.join(context_dir, nested_dir)
        assert nested_temp.startswith(expected_nested)
        assert os.path.exists(nested_temp)


def test_mkdtemp_multiple_calls():
    """Test multiple mkdtemp calls in same context create different dirs."""
    with tempstore.tempdir() as context_dir:
        temp_dir1 = tempstore.mkdtemp()
        temp_dir2 = tempstore.mkdtemp()
        temp_dir3 = tempstore.mkdtemp(prefix='special_')

        # All should be different
        assert temp_dir1 != temp_dir2 != temp_dir3

        # All should exist
        assert os.path.exists(temp_dir1)
        assert os.path.exists(temp_dir2)
        assert os.path.exists(temp_dir3)

        # All should be in context directory
        assert temp_dir1.startswith(context_dir)
        assert temp_dir2.startswith(context_dir)
        assert temp_dir3.startswith(context_dir)

        # Special prefix should be honored
        assert os.path.basename(temp_dir3).startswith('special_')


def test_tempdir_exception_cleanup():
    """Test tempdir cleans up even when exception occurs."""
    temp_path = None

    with pytest.raises(ValueError):
        with tempstore.tempdir() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            assert temp_path.exists()
            raise ValueError("Test exception")

    # Directory should still be cleaned up despite exception
    assert not temp_path.exists()


def test_contextvar_isolation():
    """Test that context variable is properly isolated."""
    import threading

    results = {}

    def thread_func(thread_id):
        with tempstore.tempdir() as temp_dir:
            results[thread_id] = temp_dir
            # Create a temp directory in this thread's context
            thread_temp = tempstore.mkdtemp()
            results[f'{thread_id}_temp'] = thread_temp

    # Run in multiple threads
    threads = []
    for i in range(3):
        thread = threading.Thread(target=thread_func, args=(i,))
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    # Each thread should have different temp directories
    assert len(set(results[i] for i in range(3))) == 3

    # Each thread's mkdtemp should be in its own context
    for i in range(3):
        assert results[f'{i}_temp'].startswith(results[i])


def test_tempdir_prefix():
    """Test that tempdir uses correct prefix."""
    with tempstore.tempdir() as temp_dir:
        dir_name = os.path.basename(temp_dir)
        assert dir_name.startswith('sky-tmp')


def test_mkdtemp_with_none_parameters():
    """Test mkdtemp handles None parameters correctly."""
    with tempstore.tempdir() as context_dir:
        # All None parameters should work
        temp_dir = tempstore.mkdtemp(suffix=None, prefix=None, dir=None)
        assert temp_dir.startswith(context_dir)
        assert os.path.exists(temp_dir)


def test_tempdir_returns_string():
    """Test that tempdir yields a string path."""
    with tempstore.tempdir() as temp_dir:
        assert isinstance(temp_dir, str)
        assert os.path.isabs(temp_dir)


def test_mkdtemp_returns_string():
    """Test that mkdtemp returns a string path."""
    with tempstore.tempdir():
        temp_dir = tempstore.mkdtemp()
        assert isinstance(temp_dir, str)
        assert os.path.isabs(temp_dir)


def test_mkdtemp_creates_intermediate_dirs():
    """Test that mkdtemp creates intermediate directories when needed."""
    with tempstore.tempdir() as context_dir:
        # Test deep nested directory creation
        deep_dir = 'a/b/c/d/e'
        temp_dir = tempstore.mkdtemp(dir=deep_dir)

        # All intermediate directories should be created
        intermediate_path = os.path.join(context_dir, deep_dir)
        assert os.path.exists(intermediate_path)
        assert temp_dir.startswith(intermediate_path)

        # Verify we can create another temp dir in the same deep structure
        temp_dir2 = tempstore.mkdtemp(dir=deep_dir)
        assert temp_dir2.startswith(intermediate_path)
        assert temp_dir != temp_dir2


def test_mkdtemp_dir_with_existing_path():
    """Test mkdtemp with dir parameter when path already exists."""
    with tempstore.tempdir() as context_dir:
        # Create a subdirectory first
        subdir = 'existing_subdir'
        full_subdir = os.path.join(context_dir, subdir)
        os.makedirs(full_subdir)

        # Should work fine even if directory already exists
        temp_dir = tempstore.mkdtemp(dir=subdir)
        assert temp_dir.startswith(full_subdir)
        assert os.path.exists(temp_dir)
