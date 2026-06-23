"""Tests for `sky jobs describe <job-id>`.

Covers the human-readable detail view, the `--yaml` clean-stdout path used for
re-submission, `-o json`, the not-found error, and argument forwarding to the
SDK.
"""
import json
from unittest import mock

from click import testing as cli_testing

from sky.client.cli import command
from sky.jobs import state as job_state
from sky.schemas.api import responses

_USER_YAML = ('name: my-llama-finetune\n'
              'resources:\n'
              '  accelerators: A100:8\n'
              'run: |\n'
              '  python train.py --lr 3e-4\n')


def _make_job_record(job_id=576, **kwargs):
    defaults = dict(
        job_id=job_id,
        task_id=0,
        job_name='my-llama-finetune',
        task_name='my-llama-finetune',
        status=job_state.ManagedJobStatus.FAILED,
        submitted_at=1700000000.0,
        workspace='default',
        resources='1x A100:8',
        recovery_count=2,
        user_name='cory',
        entrypoint='python train.py --lr 3e-4',
        user_yaml=_USER_YAML,
        metadata={'git_commit': 'a1b2c3d'},
    )
    defaults.update(kwargs)
    return responses.ManagedJobRecord(**defaults)


def _install(monkeypatch, records, capture=None, stream_capture=None):
    """Mock describe + stream_and_get; optionally capture call kwargs."""

    def fake_describe(**kw):
        if capture is not None:
            capture.update(kw)
        return 'req-1'

    def fake_stream_and_get(*a, **kw):
        if stream_capture is not None:
            stream_capture.update(kw)
        return (records, len(records), {}, len(records))

    monkeypatch.setattr('sky.jobs.describe', fake_describe)
    monkeypatch.setattr('sky.client.sdk.stream_and_get', fake_stream_and_get)


def test_describe_human_view_shows_yaml_commit_and_entrypoint(monkeypatch):
    """The default view must surface the YAML, git commit, and entrypoint.

    Revert check: if the renderer dropped user_yaml or git_commit, these
    asserts fail.
    """
    _install(monkeypatch, [_make_job_record()])

    result = cli_testing.CliRunner().invoke(command.jobs_describe, ['576'])

    assert result.exit_code == 0, result.output
    assert 'Managed job 576' in result.output
    # Entrypoint and the workdir git commit are rendered.
    assert 'python train.py --lr 3e-4' in result.output
    assert 'a1b2c3d' in result.output
    # The original YAML body is included.
    assert 'name: my-llama-finetune' in result.output
    assert 'accelerators: A100:8' in result.output
    assert 'sky jobs describe 576 --yaml' in result.output
    assert 'Entrypoint' in result.output
    assert 'Git commit' in result.output


def test_describe_shows_requested_and_used_resources_and_links(monkeypatch):
    """Mirror the dashboard: requested vs. used resources, plus external links.

    Revert check: collapse back to a single 'Resources' line / drop the links
    row and these asserts fail.
    """
    record = _make_job_record(
        resources='1x A100:8',
        cluster_resources='1x AWS(p4d.24xlarge, A100:8)',
        links={'Grafana': 'https://grafana.example/d/abc'},
    )
    _install(monkeypatch, [record])

    result = cli_testing.CliRunner().invoke(command.jobs_describe, ['576'])

    assert result.exit_code == 0, result.output
    assert 'Requested' in result.output
    assert '1x A100:8' in result.output  # requested
    assert 'p4d.24xlarge' in result.output  # used / cluster resources
    assert 'Grafana: https://grafana.example/d/abc' in result.output


def test_describe_omits_git_commit_row_when_absent(monkeypatch):
    """No git commit recorded -> no 'Git commit' row (not a '-' placeholder).

    Matches how Links is handled. Revert check: render it unconditionally and
    the 'Git commit' label would appear even with no commit.
    """
    _install(monkeypatch, [_make_job_record(metadata=None)])

    result = cli_testing.CliRunner().invoke(command.jobs_describe, ['576'])

    assert result.exit_code == 0, result.output
    assert 'Git commit' not in result.output


def test_describe_yaml_only_is_clean_for_resubmission(monkeypatch):
    """`--yaml` prints only the YAML, with no header/hint/decoration.

    This output is meant to be redirected to a file and fed back into
    `sky jobs launch`, so any extra text would corrupt it.
    """
    _install(monkeypatch, [_make_job_record()])

    result = cli_testing.CliRunner().invoke(command.jobs_describe,
                                            ['576', '--yaml'])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == _USER_YAML.strip()
    assert 'Managed job' not in result.output
    assert 'Reproduce' not in result.output
    assert 'git checkout' not in result.output


def test_describe_yaml_only_errors_when_yaml_missing(monkeypatch):
    """Old jobs without a recorded YAML get a clear error, not empty output."""
    _install(monkeypatch, [_make_job_record(user_yaml=None)])

    result = cli_testing.CliRunner().invoke(command.jobs_describe,
                                            ['576', '--yaml'])

    assert result.exit_code != 0
    assert 'No task YAML' in str(result.exception)


def test_describe_json_output(monkeypatch):
    """`-o json` emits the full record including user_yaml and entrypoint."""
    _install(monkeypatch, [_make_job_record()])

    result = cli_testing.CliRunner().invoke(command.jobs_describe,
                                            ['576', '-o', 'json'])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert len(parsed) == 1
    assert parsed[0]['job_id'] == 576
    assert parsed[0]['user_yaml'] == _USER_YAML
    assert parsed[0]['entrypoint'] == 'python train.py --lr 3e-4'
    assert parsed[0]['metadata']['git_commit'] == 'a1b2c3d'


def test_describe_not_found_errors(monkeypatch):
    """A missing job exits non-zero with a clear error."""
    _install(monkeypatch, [])

    result = cli_testing.CliRunner().invoke(command.jobs_describe, ['999'])

    assert result.exit_code != 0
    assert 'not found' in str(result.exception)


def test_describe_yaml_and_json_mutually_exclusive(monkeypatch):
    _install(monkeypatch, [_make_job_record()])

    result = cli_testing.CliRunner().invoke(command.jobs_describe,
                                            ['576', '--yaml', '-o', 'json'])

    assert result.exit_code != 0
    assert 'mutually exclusive' in result.output


def test_describe_yaml_is_prettified_and_strips_metadata(monkeypatch):
    """A stored YAML with folded scalars + _metadata is cleaned up.

    The stored `user_yaml` is re-serialized by the server with multi-line
    `run`/`setup` as folded single-quote scalars. It can also carry an internal
    `_metadata` block (the no-source-YAML fallback in `Task._to_yaml_config`),
    which would otherwise round-trip a stale `git_commit` back via
    `from_yaml_config` on re-launch. describe must re-format to block literal
    style and drop `_metadata` (mirroring the dashboard) so the output reads
    like the original and is re-submittable. Revert check: without the prettify
    step the folded scalar / `_metadata` survive and these asserts fail.
    """
    import yaml
    ugly = yaml.safe_dump({
        '_metadata': {
            'git_commit': 'abc'
        },
        'name': 'train',
        'run': 'echo a\necho b\n',
    })
    _install(monkeypatch, [_make_job_record(user_yaml=ugly)])

    result = cli_testing.CliRunner().invoke(command.jobs_describe,
                                            ['576', '--yaml'])

    assert result.exit_code == 0, result.output
    # Block literal style, not folded single-quote scalar with blank lines.
    assert 'run: |' in result.output
    assert "run: '" not in result.output
    # Internal metadata is removed.
    assert '_metadata' not in result.output
    assert 'git_commit' not in result.output
    # Still valid, re-submittable YAML.
    parsed = yaml.safe_load(result.output)
    assert parsed['run'] == 'echo a\necho b\n'


def test_describe_suppresses_streamed_logs(monkeypatch):
    """The queue request's streamed logs are redirected off stdout.

    The refresh streams lines like 'Job status: RUNNING'; without an
    output_stream they would corrupt --yaml / -o json. Revert check: drop the
    output_stream kwarg and this fails.
    """
    stream_capture = {}
    _install(monkeypatch, [_make_job_record()], stream_capture=stream_capture)

    result = cli_testing.CliRunner().invoke(command.jobs_describe,
                                            ['576', '--yaml'])

    assert result.exit_code == 0, result.output
    assert stream_capture.get('output_stream') is not None


def test_describe_forwards_job_id_and_queries_all_users(monkeypatch):
    """job_id reaches the SDK, and the lookup is always across all users.

    Mirrors `sky status <cluster>`: naming a specific entity drops the owner
    filter so the lookup succeeds regardless of submitter.
    """
    capture = {}
    _install(monkeypatch, [_make_job_record(job_id=42)], capture=capture)

    result = cli_testing.CliRunner().invoke(command.jobs_describe, ['42'])

    assert result.exit_code == 0, result.output
    assert capture['job_id'] == 42
    assert capture['all_users'] is True
