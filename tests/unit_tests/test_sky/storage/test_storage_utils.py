import io
import os
import socket
import subprocess
import tempfile
import textwrap
from unittest import mock
import zipfile

import pytest

from sky import exceptions
from sky.data import storage_utils
from sky.skylet import constants


@pytest.fixture
def tmp_dir_with_files_to_ignore():
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create workdir
        dirs = [
            'remove_dir', 'dir', 'dir/subdir', 'dir/subdir/remove_dir',
            'remove_dir_pattern'
        ]
        files = [
            'remove.py',
            'remove.sh',
            'remove.a',
            'keep.py',
            'remove.a',
            'dir/keep.txt',
            'dir/remove.sh',
            'dir/keep.a',
            'dir/remove.b',
            'dir/remove.a',
            'dir/subdir/keep.b',
            'dir/subdir/remove.py',
            'remove_dir_pattern/remove.txt',
            'remove_dir_pattern/remove.a',
        ]
        # Verify that socket files are not included in the zip file
        sockets = [
            'docker.sock',
            'fsmonitor--daemon.ipc',
        ]
        for dir_name in dirs:
            os.makedirs(os.path.join(temp_dir, dir_name), exist_ok=True)
        for file_path in files:
            full_path = os.path.join(temp_dir, file_path)
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write('test content')
        for socket_path in sockets:
            full_path = os.path.join(temp_dir, socket_path)
            socket.socket(socket.AF_UNIX, socket.SOCK_STREAM).bind(full_path)

        # Create symlinks
        os.symlink(os.path.join(temp_dir, 'keep.py'),
                   os.path.join(temp_dir, 'ln-keep.py'))
        os.symlink(os.path.join(temp_dir, 'dir/keep.py'),
                   os.path.join(temp_dir, 'ln-dir-keep.py'))
        os.symlink(os.path.join(temp_dir, 'keep.py'),
                   os.path.join(temp_dir, 'dir/subdir/ln-keep.py'))

        # Symlinks for folders
        os.symlink(os.path.join(temp_dir, 'dir/subdir/ln-folder'),
                   os.path.join(temp_dir, 'ln-folder'))

        # Create empty directories
        os.makedirs(os.path.join(temp_dir, 'empty-folder'))

        yield temp_dir


def ignore_file_content():
    return textwrap.dedent("""\
    # Current directory
    /remove.py
    /remove_dir
    **/remove_dir_pattern/**
    /*.a
    /dir/*.b
    # Pattern match for all subdirectories
    *.sh
    remove.a
    """)


@pytest.fixture
def skyignore_dir(tmp_dir_with_files_to_ignore):
    skyignore_path = os.path.join(tmp_dir_with_files_to_ignore,
                                  constants.SKY_IGNORE_FILE)
    with open(skyignore_path, 'w', encoding='utf-8') as f:
        f.write(ignore_file_content())

    yield tmp_dir_with_files_to_ignore


@pytest.fixture
def gitignore_dir(tmp_dir_with_files_to_ignore):
    gitignore_path = os.path.join(tmp_dir_with_files_to_ignore, '.gitignore')
    with open(gitignore_path, 'w', encoding='utf-8') as f:
        f.write(ignore_file_content())
    # gitignore file is only picked up if the directory is a git repository
    subprocess.run(['git', 'init'],
                   cwd=tmp_dir_with_files_to_ignore,
                   check=True)

    yield tmp_dir_with_files_to_ignore


@pytest.fixture
def basic_git_repo():
    """Fixture that provides a basic initialized git repository."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Set up git repo
        subprocess.run(['git', 'init'], cwd=temp_dir, check=True)
        yield temp_dir


def test_get_excluded_files_from_skyignore_no_file():
    excluded_files = storage_utils.get_excluded_files_from_skyignore('.')
    assert not excluded_files


def test_get_excluded_files_from_skyignore(skyignore_dir):
    # Test function
    excluded_files = storage_utils.get_excluded_files_from_skyignore(
        skyignore_dir)

    print(excluded_files)
    # Validate results
    expected_excluded_files = [
        'remove.py', 'remove_dir', 'remove.sh', 'dir/remove.sh', 'dir/remove.b',
        'remove.a', 'dir/remove.a', 'remove_dir_pattern',
        'remove_dir_pattern/remove.txt', 'remove_dir_pattern/remove.a'
    ]
    assert set(excluded_files) == set(expected_excluded_files)


def test_get_excluded_files_from_gitignore(gitignore_dir):
    # Test function
    excluded_files = storage_utils.get_excluded_files_from_gitignore(
        gitignore_dir)

    # Validate results
    expected_excluded_files = [
        'remove.py', 'remove_dir/', 'remove.sh', 'dir/remove.sh',
        'dir/remove.b', 'remove.a', 'dir/remove.a',
        'remove_dir_pattern/remove.txt', 'remove_dir_pattern/remove.a',
        'remove_dir_pattern/'
    ]
    print(sorted(excluded_files))
    print(sorted(expected_excluded_files))

    for file_path in expected_excluded_files:
        assert file_path in excluded_files
    assert len(excluded_files) == len(expected_excluded_files)


def test_skyignore_supports_negation(tmp_path):
    workdir = tmp_path / 'proj'
    workdir.mkdir()
    (workdir / '.skyignore').write_text('*.log\n!keep.log\n', encoding='utf-8')
    (workdir / 'keep.log').write_text('keep', encoding='utf-8')
    (workdir / 'drop.log').write_text('drop', encoding='utf-8')

    excluded = storage_utils.get_excluded_files_from_skyignore(str(workdir))

    assert 'drop.log' in excluded
    assert 'keep.log' not in excluded


def test_nested_skyignore_without_root(tmp_path):
    root = tmp_path / 'project'
    nested = root / 'sub' / 'module'
    nested.mkdir(parents=True)
    (nested / '.skyignore').write_text('*.tmp\n!keep.tmp\n', encoding='utf-8')
    (nested / 'example.tmp').write_text('tmp', encoding='utf-8')
    (nested / 'keep.tmp').write_text('keep', encoding='utf-8')

    excluded = storage_utils.get_excluded_files_from_skyignore(str(root))

    assert 'sub/module/example.tmp' in excluded
    assert 'sub/module/keep.tmp' not in excluded


@pytest.mark.parametrize('ignore_dir_name', ['skyignore_dir', 'gitignore_dir'])
def test_zip_files_and_folders(ignore_dir_name, request):
    ignore_dir = request.getfixturevalue(ignore_dir_name)
    log_file = io.StringIO()
    with tempfile.NamedTemporaryFile('wb+', suffix='.zip') as f:
        storage_utils.zip_files_and_folders([ignore_dir], f, log_file)
        # Print out all files in the zip
        f.seek(0)
        with zipfile.ZipFile(f, 'r') as zipf:
            actual_zipped_files = zipf.namelist()

        expected_zipped_files = [
            'ln-keep.py', 'ln-dir-keep.py', 'dir/subdir/ln-keep.py',
            'dir/subdir/remove.py', 'keep.py', 'dir/keep.txt', 'dir/keep.a',
            'dir/subdir/keep.b', 'ln-folder', 'empty-folder/', 'dir/',
            'dir/subdir/', 'dir/subdir/remove_dir/'
        ]
        if ignore_dir_name == 'gitignore_dir':
            expected_zipped_files.append(constants.GIT_IGNORE_FILE)
        else:
            expected_zipped_files.append(constants.SKY_IGNORE_FILE)

        expected_zipped_file_paths = []
        for filename in expected_zipped_files:
            file_path = os.path.join(ignore_dir, filename)
            if 'ln' not in filename:
                file_path = file_path.lstrip('/')
            expected_zipped_file_paths.append(file_path)

        processed_actual_zipped_files = []
        for file_path in actual_zipped_files:
            if '.git/' in file_path:
                # Ignore git files
                continue
            processed_actual_zipped_files.append(file_path)

        print(
            set(processed_actual_zipped_files) -
            set(expected_zipped_file_paths))
        print(
            set(expected_zipped_file_paths) -
            set(processed_actual_zipped_files))

        assert not (set(processed_actual_zipped_files) -
                    set(expected_zipped_file_paths))
        assert not (set(expected_zipped_file_paths) -
                    set(processed_actual_zipped_files))
        assert len(processed_actual_zipped_files) == len(
            expected_zipped_file_paths)
        # Check the log file correctly logs the zipped files
        log_file.seek(0)
        log_file_content = log_file.read()
        assert f'Zipped {ignore_dir}' in log_file_content


def test_zip_files_and_folders_excluded_directories():
    """Test that files inside excluded directories are not included in zip file.
    
    File/directory structure:
        temp_dir/ (temporary directory)
        └── main_dir/
            ├── main_file.txt         # contains "main file content"
            ├── .skyignore            # contains "excluded_dir"
            └── excluded_dir/         # this directory should be excluded
                ├── excluded_file.txt # contains "excluded file content" 
                └── nested_dir/
                    └── nested_file.txt # contains "nested file content"

    .skyignore content: 
        excluded_dir
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a directory structure with nested files
        main_dir = os.path.join(temp_dir, 'main_dir')
        excluded_dir = os.path.join(main_dir, 'excluded_dir')
        nested_dir = os.path.join(excluded_dir, 'nested_dir')

        # Create directories
        for dir_path in [main_dir, excluded_dir, nested_dir]:
            os.makedirs(dir_path, exist_ok=True)

        # Create files in each directory
        with open(os.path.join(main_dir, 'main_file.txt'),
                  'w',
                  encoding='utf-8') as f:
            f.write('main file content')

        with open(os.path.join(excluded_dir, 'excluded_file.txt'),
                  'w',
                  encoding='utf-8') as f:
            f.write('excluded file content')

        with open(os.path.join(nested_dir, 'nested_file.txt'),
                  'w',
                  encoding='utf-8') as f:
            f.write('nested file content')

        # Create a skyignore file that excludes the directory
        skyignore_content = "excluded_dir\n"
        skyignore_path = os.path.join(main_dir, constants.SKY_IGNORE_FILE)
        with open(skyignore_path, 'w', encoding='utf-8') as f:
            f.write(skyignore_content)

        # Create a temporary zip file
        log_file = io.StringIO()
        with tempfile.NamedTemporaryFile('wb+', suffix='.zip') as zip_file:
            # Zip the main directory
            storage_utils.zip_files_and_folders([main_dir], zip_file, log_file)

            # Examine the zip content
            zip_file.seek(0)
            with zipfile.ZipFile(zip_file, 'r') as zipf:
                zipped_files = zipf.namelist()

            # Check for included files (using path suffixes)
            assert any(
                path.endswith('main_dir/main_file.txt')
                for path in zipped_files)
            assert any(
                path.endswith(f'main_dir/{constants.SKY_IGNORE_FILE}')
                for path in zipped_files)

            # Check that excluded files are NOT included
            assert not any(
                path.endswith('excluded_file.txt') for path in zipped_files)
            assert not any(
                path.endswith('nested_file.txt') for path in zipped_files)

            # Double-check by verifying the total number of files
            # We should only have the main directory files
            assert len(zipped_files) == 2  # main_file.txt and .skyignore


def test_get_excluded_files_from_gitignore_with_submodules():
    """Test gitignore exclusion with submodules."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Set up main repo
        subprocess.run(['git', 'init'], cwd=temp_dir, check=True)

        # Create .gitignore FIRST
        gitignore_content = """
*.pyc
__pycache__/
test.log
"""
        with open(os.path.join(temp_dir, '.gitignore'), 'w') as f:
            f.write(gitignore_content)

        # Create some files
        with open(os.path.join(temp_dir, 'main.py'), 'w') as f:
            f.write('print("main")')
        with open(os.path.join(temp_dir, 'test.pyc'), 'w') as f:
            f.write('compiled')
        with open(os.path.join(temp_dir, 'test.log'), 'w') as f:
            f.write('log')

        # Now add - ignored files won't be added
        subprocess.run(['git', 'add', '.'], cwd=temp_dir, check=True)

        # Create and set up submodule
        submodule_dir = os.path.join(temp_dir, 'submod')
        os.makedirs(submodule_dir)
        subprocess.run(['git', 'init'], cwd=submodule_dir, check=True)

        # Create .gitignore in submodule FIRST
        sub_gitignore_content = """
*.txt
temp/
"""
        with open(os.path.join(submodule_dir, '.gitignore'), 'w') as f:
            f.write(sub_gitignore_content)

        # Create some files in submodule
        with open(os.path.join(submodule_dir, 'sub.py'), 'w') as f:
            f.write('print("sub")')
        with open(os.path.join(submodule_dir, 'data.txt'), 'w') as f:
            f.write('data')

        # Add and commit submodule - ignored files won't be added
        subprocess.run(['git', 'add', '.'], cwd=submodule_dir, check=True)
        subprocess.run(['git', 'config', 'user.name', 'Test User'],
                       cwd=submodule_dir,
                       check=True)
        subprocess.run(['git', 'config', 'user.email', 'test@example.com'],
                       cwd=submodule_dir,
                       check=True)
        subprocess.run(['git', 'commit', '-m', 'Initial submodule commit'],
                       cwd=submodule_dir,
                       check=True)

        # Add submodule to main repo
        subprocess.run(['git', 'submodule', 'add', './submod'],
                       cwd=temp_dir,
                       check=True)

        # Test gitignore exclusions
        excluded_files = storage_utils.get_excluded_files_from_gitignore(
            temp_dir)
        norm_excluded_files = [os.path.normpath(f) for f in excluded_files]

        # Check files from main repo's .gitignore
        assert 'test.pyc' in norm_excluded_files
        assert 'test.log' in norm_excluded_files

        # Check files from submodule's .gitignore
        assert os.path.join('submod', 'data.txt') in norm_excluded_files


def test_get_excluded_files_no_git():
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create .gitignore but don't initialize git
        with open(os.path.join(temp_dir, '.gitignore'), 'w') as f:
            f.write('*.pyc\n')

        with mock.patch('sky.data.storage_utils.logger') as mock_logger:
            excluded_files = storage_utils.get_excluded_files_from_gitignore(
                temp_dir)

        assert excluded_files == []
        mock_logger.warning.assert_called_once()
        assert '.gitignore file will be ignored' in mock_logger.warning.call_args[
            0][0]
        assert storage_utils._USE_SKYIGNORE_HINT in mock_logger.warning.call_args[
            0][0]


def test_get_excluded_files_git_not_installed():
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create .gitignore
        with open(os.path.join(temp_dir, '.gitignore'), 'w') as f:
            f.write('*.pyc\n')

        def mock_run(*args, **kwargs):
            raise subprocess.CalledProcessError(
                exceptions.COMMAND_NOT_FOUND_EXIT_CODE,
                cmd='git',
                stderr='git: command not found')

        with mock.patch('subprocess.run', side_effect=mock_run):
            with mock.patch('sky.data.storage_utils.logger') as mock_logger:
                excluded_files = storage_utils.get_excluded_files_from_gitignore(
                    temp_dir)

        assert excluded_files == []
        mock_logger.warning.assert_called_once()
        assert 'git is not installed' in mock_logger.warning.call_args[0][0]
        assert storage_utils._USE_SKYIGNORE_HINT in mock_logger.warning.call_args[
            0][0]


def test_get_excluded_files_with_negation(basic_git_repo):
    """Test negation patterns in .gitignore."""
    temp_dir = basic_git_repo

    # Create .gitignore with negation pattern
    with open(os.path.join(temp_dir, '.gitignore'), 'w') as f:
        f.write("""
*.txt
!important.txt
""")

    # Create files
    for filename in ['test.txt', 'data.txt', 'important.txt']:
        with open(os.path.join(temp_dir, filename), 'w') as fh:
            fh.write('content')

    # Check excluded files
    excluded_files = storage_utils.get_excluded_files_from_gitignore(temp_dir)
    norm_excluded_files = [os.path.normpath(f) for f in excluded_files]

    # test.txt and data.txt should be ignored, but not important.txt
    assert 'test.txt' in norm_excluded_files
    assert 'data.txt' in norm_excluded_files
    assert 'important.txt' not in norm_excluded_files


def test_get_excluded_files_from_subdirectory(basic_git_repo):
    """Test exclusion from a subdirectory with its own .gitignore."""
    temp_dir = basic_git_repo

    # Create subdir with its own .gitignore
    subdir = os.path.join(temp_dir, 'subdir')
    os.makedirs(subdir)
    with open(os.path.join(subdir, '.gitignore'), 'w') as f:
        f.write('file.txt\n')

    # Create the file that should be ignored
    with open(os.path.join(subdir, 'file.txt'), 'w') as f:
        f.write('test content')

    # Call get_excluded_files_from_gitignore on the subdir
    excluded_files = storage_utils.get_excluded_files_from_gitignore(subdir)
    assert 'file.txt' in [os.path.normpath(f) for f in excluded_files]


def test_empty_gitignore_file(basic_git_repo):
    """Test that empty .gitignore files don't cause issues."""
    temp_dir = basic_git_repo

    # Create empty .gitignore
    with open(os.path.join(temp_dir, '.gitignore'), 'w') as f:
        f.write('')  # Empty file

    # Create some files
    with open(os.path.join(temp_dir, 'test.txt'), 'w') as f:
        f.write('test content')

    # Empty .gitignore should not exclude any files
    excluded_files = storage_utils.get_excluded_files_from_gitignore(temp_dir)
    assert not excluded_files, f"Expected no excluded files, got {excluded_files}"


def test_get_excluded_files_with_nested_gitignores(basic_git_repo):
    """Test handling of nested directories with multiple .gitignore files."""
    temp_dir = basic_git_repo

    # Helper function to create file with content
    def create_file(path, content='content'):
        with open(path, 'w') as f:
            f.write(content)

    # Create root .gitignore
    create_file(os.path.join(temp_dir, '.gitignore'), '*.log\n')

    # Create nested directory structure with different .gitignore files
    level1 = os.path.join(temp_dir, 'level1')
    level2 = os.path.join(level1, 'level2')
    os.makedirs(level1)
    os.makedirs(level2)

    # Create .gitignore files at each level
    create_file(os.path.join(level1, '.gitignore'), '*.tmp\n')
    create_file(os.path.join(level2, '.gitignore'), '*.dat\n')

    # Create test files at each level
    create_file(os.path.join(temp_dir, 'root.log'), 'root log')
    create_file(os.path.join(temp_dir, 'root.tmp'), 'root tmp')

    create_file(os.path.join(level1, 'level1.log'), 'level1 log')
    create_file(os.path.join(level1, 'level1.tmp'), 'level1 tmp')
    create_file(os.path.join(level1, 'level1.dat'), 'level1 dat')

    create_file(os.path.join(level2, 'level2.log'), 'level2 log')
    create_file(os.path.join(level2, 'level2.tmp'), 'level2 tmp')
    create_file(os.path.join(level2, 'level2.dat'), 'level2 dat')

    # Test from root directory
    excluded_files = storage_utils.get_excluded_files_from_gitignore(temp_dir)
    norm_excluded_files = [os.path.normpath(f) for f in excluded_files]

    # All .log files should be excluded via root gitignore
    assert 'root.log' in norm_excluded_files
    assert os.path.join('level1', 'level1.log') in norm_excluded_files
    assert os.path.join('level1', 'level2', 'level2.log') in norm_excluded_files

    # All .tmp files should be excluded via level1 gitignore
    assert 'root.tmp' not in norm_excluded_files  # Not excluded at root level
    assert os.path.join('level1', 'level1.tmp') in norm_excluded_files
    assert os.path.join('level1', 'level2', 'level2.tmp') in norm_excluded_files

    # All .dat files should be excluded via level2 gitignore
    assert os.path.join(
        'level1',
        'level1.dat') not in norm_excluded_files  # Not excluded at level1
    assert os.path.join('level1', 'level2', 'level2.dat') in norm_excluded_files

    # Test from level1 directory
    excluded_files = storage_utils.get_excluded_files_from_gitignore(level1)
    norm_excluded_files = [os.path.normpath(f) for f in excluded_files]

    # Both .tmp and .log files should be excluded
    assert 'level1.log' in norm_excluded_files  # From parent .gitignore
    assert 'level1.tmp' in norm_excluded_files  # From level1 .gitignore
    assert os.path.join(
        'level2', 'level2.dat') in norm_excluded_files  # From level2 .gitignore


def test_complex_negation_patterns(basic_git_repo):
    """Test complex negation patterns in .gitignore, including directory-specific patterns."""
    temp_dir = basic_git_repo

    # Helper function to create file with content
    def create_file(path, content='content'):
        with open(path, 'w') as f:
            f.write(content)

    # Create directory structure
    src_dir = os.path.join(temp_dir, 'src')
    test_dir = os.path.join(src_dir, 'test')
    docs_dir = os.path.join(temp_dir, 'docs')

    for d in [src_dir, test_dir, docs_dir]:
        os.makedirs(d)

    # Create .gitignore with complex negation patterns
    create_file(
        os.path.join(temp_dir, '.gitignore'), """
# Ignore all .txt files
*.txt

# But not in the docs directory
!docs/*.txt

# Ignore all files in src directory
src/*

# But not .py files in src
!src/*.py

# Except for test_*.py files
src/test_*.py

# But allow specific test files
!src/test_important.py
""")

    # Create various files to test patterns
    files_to_create = [
        # Root dir files
        os.path.join(temp_dir, 'readme.txt'),
        os.path.join(temp_dir, 'config.py'),

        # docs dir files
        os.path.join(docs_dir, 'manual.txt'),
        os.path.join(docs_dir, 'guide.md'),

        # src dir files
        os.path.join(src_dir, 'main.py'),
        os.path.join(src_dir, 'utils.py'),
        os.path.join(src_dir, 'test_main.py'),
        os.path.join(src_dir, 'test_important.py'),
        os.path.join(src_dir, 'data.txt'),

        # src/test dir files
        os.path.join(test_dir, 'test_utils.py'),
        os.path.join(test_dir, 'fixture.txt'),
    ]

    for f in files_to_create:
        create_file(f)

    # Check excluded files
    excluded_files = storage_utils.get_excluded_files_from_gitignore(temp_dir)
    norm_excluded_files = [os.path.normpath(f) for f in excluded_files]

    # Files that should be excluded (based on actual git behavior)
    should_exclude = [
        'readme.txt',  # *.txt
        os.path.join('src', 'data.txt'),  # src/* and *.txt
        os.path.join('src', 'test_main.py'),  # src/test_*.py
    ]

    # Files that should NOT be excluded
    should_not_exclude = [
        'config.py',  # Not matched by any pattern
        os.path.join('docs', 'manual.txt'),  # !docs/*.txt
        os.path.join('docs', 'guide.md'),  # Not matched by any pattern
        os.path.join('src', 'main.py'),  # !src/*.py
        os.path.join('src', 'utils.py'),  # !src/*.py
        os.path.join('src', 'test_important.py'),  # !src/test_important.py
    ]

    # Assert all files that should be excluded are in the list
    for f in should_exclude:
        assert f in norm_excluded_files, f"Expected {f} to be excluded"

    # Assert all files that should not be excluded are not in the list
    for f in should_not_exclude:
        assert f not in norm_excluded_files, f"Expected {f} not to be excluded"

    # Check the src/test directory
    # Git might represent it either as individual files or as a directory pattern
    is_test_dir_excluded = False

    # Option 1: The test directory is excluded as a wildcard pattern
    test_dir_patterns = [
        os.path.join('src', 'test', '*'),
        os.path.join('src', 'test')
    ]
    if any(pattern in norm_excluded_files for pattern in test_dir_patterns):
        is_test_dir_excluded = True

    # Option 2: Individual files within the test directory are excluded
    elif (os.path.join('src', 'test', 'fixture.txt') in norm_excluded_files and
          os.path.join('src', 'test', 'test_utils.py') in norm_excluded_files):
        is_test_dir_excluded = True

    assert is_test_dir_excluded, "Expected src/test directory contents to be excluded"


def test_get_excluded_files_for_file():
    with pytest.raises(ValueError):
        storage_utils.get_excluded_files(
            'tests/unit_tests/sky/storage/test_storage_utils.py')


def test_get_excluded_files_for_non_existent_dir():
    with pytest.raises(ValueError):
        storage_utils.get_excluded_files('tests/non_existent_dir')
