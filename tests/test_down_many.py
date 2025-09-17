import re
import click
import pytest

from sky import exceptions
from sky.client.cli import command as cli_mod
from sky.client.cli.command import _down_or_stop_clusters


def strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


class DummyCloudError(exceptions.CloudError):
    def __init__(self):
        super().__init__(
            message="Request error UNAUTHENTICATED: Invalid token",
            cloud_provider="nebius",
            error_type="RequestError",
        )


@pytest.mark.parametrize("mode", ["down", "stop", "autostop"])
def test_batch_continues_on_errors_helper(monkeypatch, capsys, mode):
    monkeypatch.setenv("RICH_FORCE_TERMINAL", "1")
    monkeypatch.setenv("RICH_PROGRESS_NO_CLEAR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")

    names = ["sky-ok-1", "sky-nebius-fail", "sky-ok-2"]

    def fake_down(name, purge=False):
        if name == "sky-nebius-fail":
            raise DummyCloudError()

    def fake_stop(name, purge=False):
        return fake_down(name, purge=purge)

    def fake_autostop(name, idle_minutes, wait_for, down):
        return fake_down(name)

    monkeypatch.setattr(cli_mod.core, "down", fake_down, raising=True)
    monkeypatch.setattr(cli_mod.core, "stop", fake_stop, raising=True)
    monkeypatch.setattr(cli_mod.core, "autostop", fake_autostop, raising=True)

    def fake_get_cluster_records_and_set_ssh_config(clusters=None, all_users=False):
        clusters = clusters or names
        return [{"name": n, "status": None} for n in clusters]

    monkeypatch.setattr(
        cli_mod,
        "_get_cluster_records_and_set_ssh_config",
        fake_get_cluster_records_and_set_ssh_config,
        raising=True,
    )

    class FakeControllers:
        @staticmethod
        def from_name(name):
            return None

    monkeypatch.setattr(
        cli_mod,
        "controller_utils",
        type("X", (), {"Controllers": FakeControllers}),
        raising=True,
    )

    kwargs = dict(
        names=names,
        apply_to_all=False,
        all_users=False,
        down=(mode == "down"),
        no_confirm=True,
        purge=False,
        idle_minutes_to_autostop=(10 if mode == "autostop" else None),
        wait_for=None,
        async_call=False,
    )

    with pytest.raises(click.ClickException):
        _down_or_stop_clusters(**kwargs)

    captured = capsys.readouterr()
    out_raw = (captured.out + captured.err).replace("\r", "\n")

    import sys

    print("\n=== DEBUG: RAW OUTPUT ===", file=sys.stderr)
    print(repr(out_raw), file=sys.stderr)
    print("\n=== DEBUG: CLEAN OUTPUT ===", file=sys.stderr)
    print(strip_ansi(out_raw), file=sys.stderr)
    print("=" * 50, file=sys.stderr)

    with capsys.disabled():
        print("\n=== USER-VISIBLE OUTPUT (raw) ===\n")
        print(out_raw)
    out = strip_ansi(out_raw)

    assert "✓ sky-ok-1" in out
    assert "✓ sky-ok-2" in out
    assert "✗ sky-nebius-fail" in out
    assert re.search(r"nebius.*UNAUTHENTICATED", out, re.I)
    assert "Summary:" in out
    assert "Succeeded:" in out
    assert "Failed:" in out
