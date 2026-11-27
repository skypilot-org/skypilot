import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
from unittest import mock
import uuid

import pytest

from sky.backends import wheel_utils
from sky.skylet import constants


def test_build_wheels():
    shutil.rmtree(wheel_utils.WHEEL_DIR, ignore_errors=True)
    start = time.time()
    wheel_path, _ = wheel_utils.build_sky_wheel()
    assert wheel_path.exists()
    duration = time.time() - start

    start = time.time()
    wheel_path, _ = wheel_utils.build_sky_wheel()
    assert wheel_path.exists()
    duration_cached = time.time() - start

    assert duration_cached < duration

    # simulate uncleaned symlinks due to interruption
    shutil.rmtree(wheel_utils.WHEEL_DIR, ignore_errors=True)
    wheel_utils.WHEEL_DIR.mkdir()
    (wheel_utils.WHEEL_DIR / 'sky').symlink_to(wheel_utils.SKY_PACKAGE_PATH,
                                               target_is_directory=True)
    for root, _, _ in os.walk(str(wheel_utils.WHEEL_DIR)):
        # set file date to 1970-01-01 00:00 UTC
        os.utime(root, (0, 0))
    wheel_path, _ = wheel_utils.build_sky_wheel()
    assert wheel_path.exists()

    shutil.rmtree(wheel_utils.WHEEL_DIR, ignore_errors=True)


def test_unchanged_rebuild_refreshes_cache():
    """A source mtime bump that leaves the wheel unchanged rebuilds once.

    Creating a ``__pycache__`` directory at runtime (the first import of a
    module in a package directory) bumps that directory's mtime without
    changing the wheel. The rebuild it triggers produces the cached hash, and
    the cache must then count as fresh again.
    """
    shutil.rmtree(wheel_utils.WHEEL_DIR, ignore_errors=True)
    try:
        _, wheel_hash = wheel_utils.build_sky_wheel()

        time.sleep(1.1)
        os.utime(wheel_utils.SKY_PACKAGE_PATH / 'backends')

        with mock.patch.object(wheel_utils,
                               '_build_sky_wheel',
                               wraps=wheel_utils._build_sky_wheel) as build:
            _, rebuilt_hash = wheel_utils.build_sky_wheel()
            assert build.call_count == 1
            assert rebuilt_hash == wheel_hash

            for _ in range(2):
                wheel_utils.build_sky_wheel()
            assert build.call_count == 1
    finally:
        shutil.rmtree(wheel_utils.WHEEL_DIR, ignore_errors=True)


def test_pip_missing_uv_environment():
    """Test error handling when pip module is not found in UV environment."""
    with mock.patch('subprocess.run') as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=[sys.executable, '-m', 'pip'],
            stderr='/path/to/python: No module named pip')

        with mock.patch('shutil.which') as mock_which:
            # Simulate UV is available
            mock_which.return_value = '/usr/bin/uv'

            with pytest.raises(RuntimeError) as exc_info:
                wheel_utils._build_sky_wheel()

            error_msg = str(exc_info.value)
            assert 'pip module not found' in error_msg
            assert 'Since you have UV installed' in error_msg
            assert 'uv pip install pip' in error_msg


def test_pip_missing_conda_environment():
    """Test error handling when pip module is not found in conda environment."""
    with mock.patch('subprocess.run') as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=[sys.executable, '-m', 'pip'],
            stderr='/path/to/python: No module named pip')

        with mock.patch('shutil.which') as mock_which:

            def which_side_effect(cmd):
                if cmd == 'uv':
                    return None
                elif cmd == 'conda':
                    return '/usr/bin/conda'
                return None

            mock_which.side_effect = which_side_effect

            with pytest.raises(RuntimeError) as exc_info:
                wheel_utils._build_sky_wheel()

            error_msg = str(exc_info.value)
            assert 'pip module not found' in error_msg
            assert 'Since you have conda installed' in error_msg
            assert 'conda install pip' in error_msg


def test_pip_missing_no_package_manager():
    """Test error handling when pip is missing with no known package manager."""
    with mock.patch('subprocess.run') as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=[sys.executable, '-m', 'pip'],
            stderr='/path/to/python: No module named pip')

        with mock.patch('shutil.which') as mock_which:
            mock_which.return_value = None

            with pytest.raises(RuntimeError) as exc_info:
                wheel_utils._build_sky_wheel()

            error_msg = str(exc_info.value)
            assert 'pip module not found' in error_msg
            assert 'Please install pip for your Python environment' in error_msg
            assert sys.executable in error_msg


def test_pip_command_other_failure():
    """Test error handling when pip command fails for non-module reasons."""
    with mock.patch('subprocess.run') as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=[sys.executable, '-m', 'pip', 'wheel'],
            stderr=
            'ERROR: Directory /tmp/something is not installable. Neither setup.py nor pyproject.toml found.'
        )

        with pytest.raises(RuntimeError) as exc_info:
            wheel_utils._build_sky_wheel()

        error_msg = str(exc_info.value)
        assert 'pip wheel command failed' in error_msg
        assert 'Directory /tmp/something is not installable' in error_msg


def test_python_executable_not_found():
    """Test error handling when Python executable is not found (rare case)."""
    with mock.patch('subprocess.run') as mock_run:
        mock_run.side_effect = FileNotFoundError(
            "[Errno 2] No such file or directory: '/nonexistent/python'")

        with pytest.raises(RuntimeError) as exc_info:
            wheel_utils._build_sky_wheel()

        error_msg = str(exc_info.value)
        assert 'Python executable not found' in error_msg
        assert sys.executable in error_msg


def test_python_m_pip_usage():
    """Test that we use 'python -m pip' instead of 'pip3'."""
    # This is a simpler test that just verifies the command format
    # The actual wheel building is tested in test_wheels.py

    # Create a minimal mock that captures the subprocess.run call
    with mock.patch('subprocess.run') as mock_run:
        # Make subprocess.run fail with a specific error so we can catch it
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=[sys.executable, '-m', 'pip'],
            stderr='Intentional test failure')

        try:
            wheel_utils._build_sky_wheel()
        except RuntimeError:
            # We expect this to fail, we just want to check the command
            pass

        # Verify that subprocess.run was called with python -m pip
        mock_run.assert_called()
        call_args = mock_run.call_args[0][0]

        # Check the command format
        assert call_args[0] == sys.executable
        assert call_args[1:3] == ['-m', 'pip']
        assert 'wheel' in call_args
        assert '--no-deps' in call_args

        # Verify we're NOT using pip3
        assert 'pip3' not in call_args


def test_wheel_build_reproducible():
    """Test that wheel builds are reproducible across separate processes.

    PYTHONHASHSEED is randomized per-process, so without the fix (setting
    SOURCE_DATE_EPOCH and PYTHONHASHSEED in the pip subprocess env), two
    separate processes building the same source can produce different wheel
    hashes due to non-deterministic metadata ordering and zip timestamps.

    We simulate this by running each build in a separate subprocess,
    just like different API server processes would. Each subprocess gets
    a naturally randomized PYTHONHASHSEED.
    """
    build_script = (
        'import shutil, os; '
        'from sky.backends import wheel_utils; '
        'shutil.rmtree(str(wheel_utils.WHEEL_DIR), ignore_errors=True); '
        '_, h = wheel_utils.build_sky_wheel(); '
        'print(h)')

    try:
        result1 = subprocess.run([sys.executable, '-c', build_script],
                                 capture_output=True,
                                 text=True,
                                 check=True)
        hash1 = result1.stdout.strip().splitlines()[-1]

        result2 = subprocess.run([sys.executable, '-c', build_script],
                                 capture_output=True,
                                 text=True,
                                 check=True)
        hash2 = result2.stdout.strip().splitlines()[-1]

        assert hash1 == hash2, (
            f'Wheel build is not reproducible across processes: '
            f'{hash1} != {hash2}. '
            'Check that SOURCE_DATE_EPOCH and PYTHONHASHSEED are set in the '
            'pip wheel subprocess environment in _build_sky_wheel().')
    finally:
        shutil.rmtree(wheel_utils.WHEEL_DIR, ignore_errors=True)


_REPO_ROOT = str(pathlib.Path(wheel_utils.__file__).resolve().parents[2])


def _wheel_paths_in_subprocess(tmp_path, runtime_dir):
    """Imports wheel_utils fresh, since WHEEL_DIR is resolved at import."""
    script = ('import json; '
              'from sky.backends import wheel_utils; '
              'from sky.server import local_disk; '
              'print(json.dumps({"file": wheel_utils.__file__, '
              '"wheel_dir": str(wheel_utils.WHEEL_DIR), '
              '"lock": str(wheel_utils._WHEEL_LOCK_PATH), '
              '"local_disk": local_disk.local_roots()["wheels"]}))')
    env = dict(os.environ, HOME=str(tmp_path / 'home'), PYTHONPATH=_REPO_ROOT)
    env.pop(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, None)
    if runtime_dir is not None:
        env[constants.SKY_RUNTIME_DIR_ENV_VAR_KEY] = str(runtime_dir)
    result = subprocess.run([sys.executable, '-c', script],
                            capture_output=True,
                            text=True,
                            check=True,
                            cwd=str(tmp_path),
                            env=env)
    paths = json.loads(result.stdout.strip().splitlines()[-1])
    assert pathlib.Path(paths['file']).resolve() == pathlib.Path(
        wheel_utils.__file__).resolve()
    return paths


@pytest.mark.parametrize('runtime_dir', [None, 'runtime'])
def test_wheel_dir_follows_runtime_dir(tmp_path, runtime_dir):
    """Isolated API servers sharing a HOME must not share a wheel cache."""
    root = tmp_path / ('home' if runtime_dir is None else runtime_dir)
    paths = _wheel_paths_in_subprocess(
        tmp_path, None if runtime_dir is None else tmp_path / runtime_dir)
    assert paths['wheel_dir'] == str(root / '.sky' / 'wheels')
    assert paths['lock'] == str(root / '.sky' / '.wheels_lock')
    # The server's local-disk accounting keeps its own copy of this path.
    assert paths['local_disk'] == paths['wheel_dir']


@pytest.fixture
def fake_wheel_cache(tmp_path, monkeypatch):
    """Points the cache at tmp_path and stubs out the real wheel build."""
    wheel_dir = tmp_path / 'wheels'
    monkeypatch.setattr(wheel_utils, 'WHEEL_DIR', wheel_dir)
    monkeypatch.setattr(wheel_utils, '_WHEEL_LOCK_PATH',
                        tmp_path / '.wheels_lock')
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path / 'tmp'))
    (tmp_path / 'tmp').mkdir()
    wheel_name = wheel_utils._WHEEL_PATTERN.replace('*', 'py3-none-any')
    builds = []

    def fake_build():
        wheel = wheel_dir / uuid.uuid4().hex / wheel_name
        wheel.parent.mkdir(parents=True)
        wheel.write_text(str(wheel_utils.SKY_PACKAGE_PATH))
        builds.append(wheel.parent.name)
        return wheel

    monkeypatch.setattr(wheel_utils, '_build_sky_wheel', fake_build)
    return builds


def _make_source_tree(path, age_seconds):
    (path / 'sky').mkdir(parents=True)
    (path / 'sky' / '__init__.py').write_text('')
    stamp = time.time() - age_seconds
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            os.utime(os.path.join(root, name), (stamp, stamp))
    return path / 'sky'


def test_cache_not_reused_across_source_dirs(tmp_path, monkeypatch,
                                             fake_wheel_cache):
    """Switching checkouts must not ship the previous checkout's wheel.

    Checkout B's source is older than the wheel built from checkout A, so
    mtimes alone say A's wheel is fresh.
    """
    src_a = _make_source_tree(tmp_path / 'a', age_seconds=7200)
    src_b = _make_source_tree(tmp_path / 'b', age_seconds=3600)

    monkeypatch.setattr(wheel_utils, 'SKY_PACKAGE_PATH', src_a)
    _, hash_a = wheel_utils.build_sky_wheel()

    monkeypatch.setattr(wheel_utils, 'SKY_PACKAGE_PATH', src_b)
    _, hash_b = wheel_utils.build_sky_wheel()

    assert fake_wheel_cache == [hash_a, hash_b]


def test_cache_not_reused_after_symlink_retarget(tmp_path, monkeypatch,
                                                 fake_wheel_cache):
    """A stable checkout symlink pointed at another checkout is a new source."""
    _make_source_tree(tmp_path / 'a', age_seconds=7200)
    _make_source_tree(tmp_path / 'b', age_seconds=3600)
    current = tmp_path / 'current'
    current.symlink_to(tmp_path / 'a', target_is_directory=True)
    monkeypatch.setattr(wheel_utils, 'SKY_PACKAGE_PATH', current / 'sky')
    _, hash_a = wheel_utils.build_sky_wheel()

    current.unlink()
    current.symlink_to(tmp_path / 'b', target_is_directory=True)
    _, hash_b = wheel_utils.build_sky_wheel()

    assert fake_wheel_cache == [hash_a, hash_b]


def test_cache_not_reused_after_unmarked_wheel_replaced_it(
        tmp_path, monkeypatch, fake_wheel_cache):
    """A SkyPilot version without the marker builds over this one's wheel.

    It writes a newer wheel and deletes the other hash dirs, but knows
    nothing of the marker.
    """
    src = _make_source_tree(tmp_path / 'a', age_seconds=3600)
    monkeypatch.setattr(wheel_utils, 'SKY_PACKAGE_PATH', src)
    _, hash_a = wheel_utils.build_sky_wheel()

    foreign = wheel_utils.WHEEL_DIR / 'foreign'
    foreign.mkdir()
    (foreign / wheel_utils._WHEEL_PATTERN.replace('*', 'py3-none-any')).touch()
    shutil.rmtree(wheel_utils.WHEEL_DIR / hash_a)

    _, rebuilt_hash = wheel_utils.build_sky_wheel()

    assert fake_wheel_cache == [hash_a, rebuilt_hash]


def test_cache_reused_for_same_source_dir(tmp_path, monkeypatch,
                                          fake_wheel_cache):
    src = _make_source_tree(tmp_path / 'a', age_seconds=3600)
    monkeypatch.setattr(wheel_utils, 'SKY_PACKAGE_PATH', src)

    hashes = {wheel_utils.build_sky_wheel()[1] for _ in range(3)}

    assert fake_wheel_cache == list(hashes)
