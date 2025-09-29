"""Unit tests for sky.utils.command_runner."""

from sky.utils import command_runner


def test_docker_runner_passes_proxy_command_to_inner_hop() -> None:
    """Ensure docker-mode runners reuse user proxy for the host hop."""
    proxy_cmd = 'ssh -W %h:%p jump@host'
    runner = command_runner.SSHCommandRunner(
        node=('10.0.0.5', 22),
        ssh_user='ubuntu',
        ssh_private_key='/tmp/fake-key',
        ssh_proxy_command=proxy_cmd,
        docker_user='container',
        ssh_control_name='unit-test-control',
    )

    # Proxy command should be consumed by the docker bridge, not the outer hop.
    assert runner._ssh_proxy_command is None  # type: ignore[attr-defined]

    # Inner hop must include the user proxy command before targeting the host VM.
    inner_cmd = runner._docker_ssh_proxy_command(['ssh', '-T'])  # type: ignore[attr-defined]
    assert "ProxyCommand='ssh -W 10.0.0.5:22 jump@host'" in inner_cmd
    assert inner_cmd.endswith('ubuntu@10.0.0.5')

    outer_cmd = runner.ssh_base_command(
        ssh_mode=command_runner.SshMode.NON_INTERACTIVE,
        port_forward=None,
        connect_timeout=None,
    )
    assert outer_cmd[-1] == 'container@localhost'
