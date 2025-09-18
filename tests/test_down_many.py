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

    monkeypatch.setattr(cli_mod, '_async_call_or_wait',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(cli_mod.sdk, "down", fake_down)
    monkeypatch.setattr(cli_mod.sdk, "stop", fake_stop)
    monkeypatch.setattr(cli_mod.sdk, "autostop", fake_autostop)

    monkeypatch.setattr(cli_mod,
                        "_get_cluster_records_and_set_ssh_config",
                        lambda clusters=None, all_users=False: [{
                            "name": n,
                            "status": None
                        } for n in (clusters or names)])

    class FakeControllers:

        @staticmethod
        def from_name(name):
            return None

    monkeypatch.setattr(cli_mod, "controller_utils",
                        type("X", (), {"Controllers": FakeControllers}))

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

    monkeypatch.setattr(
        cli_mod.sdk, "get", lambda *args, **kwargs: [{
            "name": n,
            "status": None
        } for n in names])

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

    assert re.search(r"nebius.*UNAUTHENTICATED", out, re.I)
    assert "Summary:" in out
    assert "✗ Failed: sky-nebius-fail" in out
    assert "Failed:" in out

    if mode in ("down", "stop"):
        if mode == "down":
            assert "Terminating cluster sky-ok-1...done" in out
            assert "Terminating cluster sky-ok-2...done" in out
        else:
            assert "Stopping cluster sky-ok-1...done" in out
            assert "Stopping cluster sky-ok-2...done" in out

        assert "✓ Succeeded:" in out
        summary_line = next(line for line in out.splitlines()
                            if line.strip().startswith("✓ Succeeded:"))
        succ_list = [
            n.strip() for n in summary_line.split(":", 1)[1].split(",")
        ]
        assert set(succ_list) == {"sky-ok-1", "sky-ok-2"}


    else:
        assert "✓ Succeeded:" not in out
        assert "Scheduling autostop on cluster 'sky-ok-1'...done" in out
        assert "Scheduling autostop on cluster 'sky-ok-2'...done" in out