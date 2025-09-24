"""Tests for command_runner utilities."""
from pathlib import Path

from sky.utils import command_runner


class _DummyRunner(command_runner.CommandRunner):
    """Minimal runner to exercise CommandRunner._rsync locally."""

    def run(self, *args, **kwargs):  # pragma: no cover - not used in test
        raise NotImplementedError

    def rsync(self, *args, **kwargs):  # pragma: no cover - not used in test
        raise NotImplementedError


def test_rsync_respects_skyignore_negation(tmp_path):
    src = tmp_path / 'src'
    dst = tmp_path / 'dst'
    src.mkdir()
    dst.mkdir()

    (src / '.skyignore').write_text('*.log\n!important.log\n',
                                    encoding='utf-8')
    (src / 'boring.log').write_text('boring', encoding='utf-8')
    (src / 'important.log').write_text('important', encoding='utf-8')
    (src / 'notes.txt').write_text('notes', encoding='utf-8')

    runner = _DummyRunner(node=('local', 0))
    log_path: Path = tmp_path / 'rsync.log'

    runner._rsync(
        source=str(src),
        target=str(dst),
        node_destination=None,
        up=True,
        rsh_option=None,
        log_path=str(log_path),
        stream_logs=False,
    )

    assert not (dst / 'boring.log').exists()
    assert (dst / 'important.log').exists()
    assert (dst / 'notes.txt').exists()
