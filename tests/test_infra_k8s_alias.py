"""Tests ensuring `infra: k8s` is accepted in resource YAML.

Covers:
- JSON Schema accepts `k8s` (and rejects typos like `k9s`)
- Resources.validate accepts YAML with the alias
- Parser accepts `k8s` inputs without requiring immediate cloud resolution
"""
import re
from unittest import mock

from jsonschema import validate
from jsonschema import ValidationError
import pytest
import yaml

from sky import resources as sky_resources
from sky.utils import schemas


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
    - infra: aws
  cpus: 1
run: |
  echo 'Hello SkyPilot!'
"""


@pytest.mark.parametrize(
    'yaml_text',
    [
        TASK_FULL_NAME,
        TASK_ALIAS_WITH_CTX,
        TASK_ANY_OF,
    ],
)
def test_launch_accepts_infra(yaml_text):
    config = yaml.safe_load(yaml_text)
    resources_cfg = config['resources']

    with mock.patch('sky.provision.kubernetes.utils.get_all_kube_context_names',
                    return_value=['my-context', 'default']):
        res_objs = sky_resources.Resources.from_yaml_config(resources_cfg)
        for r in (res_objs if isinstance(res_objs, list) else list(res_objs)):
            r.validate()


@pytest.mark.parametrize(
    'infra_value',
    ['k8s', 'k8s/my-context', 'K8S', 'kubernetes'],
)
def test_parser_accepts_k8s_without_resolution(infra_value):
    res_dict = {'infra': infra_value, 'cpus': 1}
    res_objs = sky_resources.Resources.from_yaml_config(res_dict)
    for r in (res_objs if isinstance(res_objs, list) else list(res_objs)):
        assert (r.infra.cloud or '').lower() == 'kubernetes'
        if '/' in infra_value:
            assert r.infra.region == 'my-context'
            assert r.infra.zone is None
            assert r.infra.to_str() == 'kubernetes/my-context'
