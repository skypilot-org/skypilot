"""The VM image bake and the image tags the clouds launch with."""
import os
import re
import subprocess

import pytest

from sky.clouds import aws as aws_mod
from sky.clouds import gcp as gcp_mod
from sky.skylet import constants

_IMAGES_DIR = os.path.join(
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    'sky', 'catalog', 'images')


def _read(*parts):
    with open(os.path.join(_IMAGES_DIR, *parts), 'r', encoding='utf-8') as f:
        return f.read()


def _sky_requirement(extra, src):
    """Runs skypilot.sh's own sky_requirement function."""
    script = _read('provisioners', 'skypilot.sh')
    match = re.search(r'^sky_requirement\(\) \{\n.*?^\}\n', script,
                      re.MULTILINE | re.DOTALL)
    assert match, 'sky_requirement() not found in skypilot.sh'
    env = {'PATH': os.environ['PATH']}
    if src is not None:
        env['SKYPILOT_SRC'] = src
    return subprocess.run(
        ['bash', '-c',
         match.group(0) + f'sky_requirement {extra}'],
        env=env,
        capture_output=True,
        text=True,
        check=True).stdout.strip()


def test_bake_installs_skypilot_from_the_given_source():
    src = 'git+https://github.com/skypilot-org/skypilot@abc123'
    assert _sky_requirement('remote', src) == f'skypilot[remote] @ {src}'
    assert _sky_requirement('aws', src) == f'skypilot[aws] @ {src}'


def test_bake_defaults_to_the_latest_nightly():
    assert _sky_requirement('remote', None) == 'skypilot-nightly[remote]'
    assert _sky_requirement('gcp', '') == 'skypilot-nightly[gcp]'


def test_every_skypilot_install_in_the_bake_honors_the_source():
    """An install that names skypilot-nightly directly would ignore
    skypilot_src, so the Ray version and patches would come from the nightly
    again."""
    script = _read('provisioners', 'skypilot.sh')
    body = re.search(r'^sky_requirement\(\) \{\n.*?^\}\n', script,
                     re.MULTILINE | re.DOTALL).group(0)
    rest = script.replace(body, '')
    installs = re.findall(
        r'pip install [^\n]*(?:skypilot|sky_requirement)[^\n]*', rest)
    # remote, plus one per cloud's extras.
    assert len(installs) == 4, installs
    for line in installs:
        assert '"$(sky_requirement ' in line, line
    assert 'skypilot-nightly[' not in rest


_TEMPLATES = [
    name for name in sorted(os.listdir(_IMAGES_DIR))
    if name.startswith('skypilot-') and name.endswith('.pkr.hcl')
]


def test_templates_are_found():
    assert len(_TEMPLATES) >= 8, _TEMPLATES


@pytest.mark.parametrize('template', _TEMPLATES)
def test_every_template_passes_skypilot_src_to_the_bake(template):
    text = _read(template)
    assert 'variable "skypilot_src"' in text
    assert '"SKYPILOT_SRC=${var.skypilot_src}"' in text


# Images with SKY_REMOTE_RAY_VERSION baked in carry this suffix, e.g. -ray256.
_MAJOR, _MINOR = constants.SKY_REMOTE_RAY_VERSION.split('.')[:2]
_BAKED_RAY_SUFFIX = f'-ray{_MAJOR}{_MINOR}'


def _aws_default_ami(monkeypatch, acc, arch):
    monkeypatch.setattr(aws_mod.catalog, 'get_image_id_from_tag',
                        lambda tag, region, clouds: tag)
    monkeypatch.setattr(aws_mod.AWS, 'get_accelerators_from_instance_type',
                        classmethod(lambda cls, instance_type: acc))
    monkeypatch.setattr(aws_mod.AWS, 'get_arch_from_instance_type',
                        classmethod(lambda cls, instance_type: arch))
    return aws_mod.AWS._get_default_ami('us-east-1', 'some-type')  # pylint: disable=protected-access


@pytest.mark.parametrize('acc,arch', [(None, 'x86_64'), (None, 'arm64'),
                                      ({
                                          'T4': 1
                                      }, 'x86_64'), ({
                                          'T4G': 1
                                      }, 'arm64')])
def test_aws_default_images_have_the_current_ray_baked(monkeypatch, acc, arch):
    """A stale default image makes every node reinstall Ray at launch.

    A major or minor bump of SKY_REMOTE_RAY_VERSION without new images fails
    here (a patch bump does not change the suffix): build them
    (sky/catalog/images/README.md) or accept the reinstall by updating this.
    """
    assert _aws_default_ami(monkeypatch, acc, arch).endswith(_BAKED_RAY_SUFFIX)


def test_gcp_default_cpu_image_has_the_current_ray_baked():
    assert gcp_mod._DEFAULT_CPU_IMAGE_ID.endswith(_BAKED_RAY_SUFFIX)  # pylint: disable=protected-access


@pytest.mark.parametrize('acc_name', ['A100', 'L4', 'T4'])
def test_gcp_default_gpu_images_have_the_current_ray_baked(acc_name):
    assert gcp_mod.GCP._get_gpu_image_id(acc_name).endswith(  # pylint: disable=protected-access
        _BAKED_RAY_SUFFIX)


def test_legacy_gpu_images_are_not_switched():
    """Pre-Turing GPUs keep the 535-driver images; they are not rebuilt."""
    assert not gcp_mod.GCP._get_gpu_image_id('V100').endswith(  # pylint: disable=protected-access
        _BAKED_RAY_SUFFIX)
    assert not aws_mod._DEFAULT_GPU_CUDA12_IMAGE_ID.endswith(  # pylint: disable=protected-access
        _BAKED_RAY_SUFFIX)
