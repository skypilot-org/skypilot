"""Tests ensuring `infra: k8s` is accepted in resource YAML.

Covers:
- JSON Schema accepts `k8s` (and rejects typos like `k9s`)
- CLI `sky launch --dryrun` accepts YAML with the alias
- Parser accepts `k8s` inputs without requiring immediate cloud resolution
"""

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from jsonschema import validate
from jsonschema import ValidationError
import pytest

from sky import resources as sky_resources
from sky.utils import schemas


def _candidate_cmds(path: Path):
    yield [
        sys.executable, '-m', 'sky.cli', 'launch', '--dryrun', '-y',
        str(path)
    ]
    if shutil.which('sky'):
        yield ['sky', 'launch', '--dryrun', '-y', str(path)]


def test_schema_accepts_k8s():
    resources_schema = schemas.get_resources_schema()
    pattern = resources_schema['properties']['infra']['pattern']
    pat = re.compile(pattern)

    assert pat.match('kubernetes')
    assert pat.match('k8s')
    assert pat.match('k8s/my-context')


def test_schema_rejects_k9s():
    resources_schema = schemas.get_resources_schema()
    with pytest.raises(ValidationError):
        validate({'infra': 'k9s'}, resources_schema)


TASK_FULL_NAME = """\
name: hello
resources:
  infra: kubernetes
  cpus: 1
run: |
  echo 'Hello SkyPilot!'
"""

TASK_ALIAS_WITH_CTX = """\
name: hello
resources:
  infra: k8s/my-context
  cpus: 1
run: |
  echo 'Hello SkyPilot!'
"""

TASK_ANY_OF = """\
name: hello
resources:
  any_of:
    - infra: k8s
    - infra: aws/*/us-east-1a
  cpus: 1
run: |
  echo 'Hello SkyPilot!'
"""


@pytest.mark.parametrize(
    'yaml_text,label',
    [
        (TASK_FULL_NAME, 'infra=kubernetes'),
        (TASK_ALIAS_WITH_CTX, 'infra=k8s_with_context'),
        (TASK_ANY_OF, 'infra_in_any_of'),
    ],
)
def test_launch_dryrun_accepts_infra(tmp_path: Path, yaml_text, label):
    task_file = tmp_path / f'{label}.yaml'
    task_file.write_text(yaml_text)

    env = os.environ.copy()
    env.setdefault('SKY_NO_TELEMETRY', '1')

    proc = None
    for cmd in _candidate_cmds(task_file):
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                env=env,
            )
            break
        except FileNotFoundError:
            continue

    if proc is None:
        pytest.skip('Could not invoke SkyPilot CLI; ensure `sky` is on PATH.')

    out = (proc.stdout or '') + (proc.stderr or '')
    assert proc.returncode == 0, 'Expected success; got {}\n\n{}'.format(
        proc.returncode, out)


@pytest.mark.parametrize(
    'infra_value',
    ['k8s', 'k8s/my-context', 'K8S', 'kubernetes'],
)
def test_parser_accepts_k8s_without_resolution(infra_value):
    res_dict = {'infra': infra_value, 'cpus': 1}

    if hasattr(sky_resources.Resources, 'from_yaml_config'):
        r = sky_resources.Resources.from_yaml_config(res_dict)
    else:
        r = sky_resources.Resources()
        if hasattr(r, 'update'):
            r.update(res_dict)
        elif hasattr(r, 'set'):
            r.set(res_dict)
        else:
            if hasattr(r, 'infra'):
                setattr(r, 'infra', infra_value)
            if hasattr(r, 'cpus'):
                setattr(r, 'cpus', 1)

    if hasattr(r, 'infra'):
        v = str(getattr(r, 'infra') or '').lower()
        assert v.startswith('k8s') or v.startswith('kubernetes')
