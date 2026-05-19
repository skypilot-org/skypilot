"""Tests for git commit metadata capture in tasks and DAGs."""
import os
import subprocess
import tempfile

from sky import task as task_lib
from sky.utils import dag_utils

_repo_counter = 0


def _init_git_repo(path):
    """Initialize a git repo with a commit and return the commit hash."""
    global _repo_counter
    _repo_counter += 1
    subprocess.run(['git', 'init'], cwd=path, capture_output=True, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@test.com'],
                   cwd=path,
                   capture_output=True,
                   check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'],
                   cwd=path,
                   capture_output=True,
                   check=True)
    # Use counter to ensure unique commits across repos.
    with open(os.path.join(path, 'dummy.txt'), 'w') as f:
        f.write(f'repo {_repo_counter}')
    subprocess.run(['git', 'add', '.'],
                   cwd=path,
                   capture_output=True,
                   check=True)
    subprocess.run(['git', 'commit', '-m', f'init repo {_repo_counter}'],
                   cwd=path,
                   capture_output=True,
                   check=True)
    result = subprocess.run(['git', 'rev-parse', 'HEAD'],
                            cwd=path,
                            capture_output=True,
                            text=True,
                            check=True)
    return result.stdout.strip()


class TestWorkdirGitCommit:
    """Tests for git commit capture via expand_and_validate_workdir."""

    def test_workdir_in_git_repo(self):
        """Git commit is captured when workdir is in a git repo."""
        with tempfile.TemporaryDirectory() as d:
            commit = _init_git_repo(d)
            t = task_lib.Task(workdir=d)
            t.expand_and_validate_workdir()
            assert t.metadata['git_commit'] == commit

    def test_workdir_not_in_git_repo(self):
        """Git commit is None when workdir is not in a git repo."""
        with tempfile.TemporaryDirectory() as d:
            t = task_lib.Task(workdir=d)
            t.expand_and_validate_workdir()
            assert t.metadata.get('git_commit') is None

    def test_no_workdir(self):
        """No git commit is captured when workdir is None."""
        t = task_lib.Task()
        t.expand_and_validate_workdir()
        assert 'git_commit' not in t.metadata

    def test_workdir_does_not_overwrite_with_none(self):
        """Server-side re-validation should not overwrite a valid commit."""
        with tempfile.TemporaryDirectory() as git_dir, \
             tempfile.TemporaryDirectory() as non_git_dir:
            commit = _init_git_repo(git_dir)

            # Simulate client-side: capture commit from git workdir.
            t = task_lib.Task(workdir=git_dir)
            t.expand_and_validate_workdir()
            assert t.metadata['git_commit'] == commit

            # Simulate server-side: workdir is remapped to a non-git dir.
            t.workdir = non_git_dir
            t.expand_and_validate_workdir()
            # Should still have the original commit.
            assert t.metadata['git_commit'] == commit

    def test_workdir_overwrites_with_valid_commit(self):
        """A new valid git commit from workdir should override a prior one."""
        with tempfile.TemporaryDirectory() as repo1, \
             tempfile.TemporaryDirectory() as repo2:
            commit1 = _init_git_repo(repo1)
            commit2 = _init_git_repo(repo2)
            assert commit1 != commit2

            t = task_lib.Task(workdir=repo1)
            t.expand_and_validate_workdir()
            assert t.metadata['git_commit'] == commit1

            # Workdir changes to a different repo — commit should update.
            t.workdir = repo2
            t.expand_and_validate_workdir()
            assert t.metadata['git_commit'] == commit2

    def test_git_url_workdir_uses_ref(self):
        """Git URL workdir stores the ref directly as git_commit."""
        t = task_lib.Task(workdir={
            'url': 'https://github.com/example/repo.git',
            'ref': 'abc123',
        })
        t.expand_and_validate_workdir()
        assert t.metadata['git_commit'] == 'abc123'

    def test_git_url_workdir_no_ref(self):
        """Git URL workdir without ref does not set git_commit."""
        t = task_lib.Task(workdir={
            'url': 'https://github.com/example/repo.git',
        })
        t.expand_and_validate_workdir()
        assert 'git_commit' not in t.metadata


class TestYamlGitCommit:
    """Tests for git commit capture from YAML file location."""

    def test_yaml_in_git_repo(self):
        """Git commit is captured from the YAML file's repo."""
        with tempfile.TemporaryDirectory() as d:
            commit = _init_git_repo(d)
            yaml_path = os.path.join(d, 'task.yaml')
            with open(yaml_path, 'w') as f:
                f.write('run: echo hi\n')

            dag = dag_utils.load_chain_dag_from_yaml(yaml_path)
            assert dag.tasks[0].metadata['git_commit'] == commit

    def test_yaml_not_in_git_repo(self):
        """No git commit when YAML is not in a git repo."""
        with tempfile.TemporaryDirectory() as d:
            yaml_path = os.path.join(d, 'task.yaml')
            with open(yaml_path, 'w') as f:
                f.write('run: echo hi\n')

            dag = dag_utils.load_chain_dag_from_yaml(yaml_path)
            assert 'git_commit' not in dag.tasks[0].metadata

    def test_yaml_does_not_override_workdir_commit(self):
        """Workdir git commit takes priority over YAML file git commit."""
        with tempfile.TemporaryDirectory() as yaml_repo, \
             tempfile.TemporaryDirectory() as workdir_repo:
            yaml_commit = _init_git_repo(yaml_repo)
            workdir_commit = _init_git_repo(workdir_repo)
            assert yaml_commit != workdir_commit

            yaml_path = os.path.join(yaml_repo, 'task.yaml')
            with open(yaml_path, 'w') as f:
                f.write(f'workdir: {workdir_repo}\nrun: echo hi\n')

            dag = dag_utils.load_chain_dag_from_yaml(yaml_path)
            t = dag.tasks[0]
            # YAML commit is set initially by load_chain_dag_from_yaml,
            # but expand_and_validate_workdir will override it with the
            # workdir's commit.
            t.expand_and_validate_workdir()
            assert t.metadata['git_commit'] == workdir_commit


class TestMetadataRoundTrip:
    """Tests that git_commit survives YAML serialization round-trip."""

    def test_metadata_survives_serialization(self):
        """git_commit in metadata persists through to_yaml_config and back."""
        with tempfile.TemporaryDirectory() as d:
            commit = _init_git_repo(d)
            t = task_lib.Task(workdir=d, run='echo hi')
            t.set_resources({})
            t.expand_and_validate_workdir()
            assert t.metadata['git_commit'] == commit

            # Serialize to YAML config and reconstruct.
            config = t.to_yaml_config()
            t2 = task_lib.Task.from_yaml_config(config)
            assert t2.metadata['git_commit'] == commit
