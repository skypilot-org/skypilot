"""Tests for Kubernetes FUSE helpers."""

from sky.provision.kubernetes import fuse


def test_fusermount_shim_setup_command_uses_supplied_sudo_and_shared_dir():
    command = fuse.get_fusermount_shim_setup_command(
        sudo_cmd='$(prefix_cmd)',
        shared_dir='/var/run/fusermount')

    assert '$(prefix_cmd) cp -p "$FUSERMOUNT_PATH"' in command
    assert ('$(prefix_cmd) ln -sf "/var/run/fusermount/fusermount-shim" '
            '"$FUSERMOUNT_PATH"') in command
    assert ('$(prefix_cmd) cp -p "/var/run/fusermount/fusermount-wrapper" '
            '/bin/fusermount-wrapper') in command
    assert 'wait_for_fusermount()' in command


def test_fusermount_shim_setup_command_allows_empty_sudo():
    command = fuse.get_fusermount_shim_setup_command(sudo_cmd='')

    assert 'sudo cp -p' not in command
    assert 'cp -p "$FUSERMOUNT_PATH"' in command
