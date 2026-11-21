"""Shell regression tests for Kubernetes setup privilege handling."""

from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('uid,has_sudo', [('0', False), ('1000', True),
                                          ('1000', False)])
def test_setup_sudo(tmp_path: Path, uid: str, has_sudo: bool) -> None:
    template = (Path(__file__).parents[3] /
                'sky/templates/kubernetes-ray.yml.j2').read_text()
    prelude = template.split('setup_commands:',
                             1)[1].split('  - |', 1)[1].split(
                                 '{%- for initial_setup_command', 1)[0]
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    for name in ('mkdir', 'touch', 'bash'):
        (bin_dir / name).symlink_to(shutil.which(name))
    id_path = bin_dir / 'id'
    id_path.write_text(f'#!/bin/sh\nprintf "{uid}\\n"\n')
    id_path.chmod(0o755)
    if has_sudo:
        sudo = bin_dir / 'sudo'
        sudo.write_text('#!/bin/sh\nexec "$@"\n')
        sudo.chmod(0o755)
    result = subprocess.run(
        ['/bin/bash', '-c', prelude + '\nsudo bash -c \'printf setup-ok\''],
        env={
            'HOME': str(tmp_path),
            'PATH': str(bin_dir)
        },
        check=False,
        capture_output=True,
        text=True)
    if uid == '0' or has_sudo:
        assert result.returncode == 0, result.stderr
        assert result.stdout == 'setup-ok'
    else:
        assert result.returncode == 127
        assert 'sudo: command not found' in result.stderr
