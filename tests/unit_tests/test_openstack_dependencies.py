"""Tests for the OpenStack optional dependency markers."""

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.version import Version

from sky.setup_files import dependencies


def _openstacksdk_requirement(python_version: str) -> Requirement:
    environment = default_environment()
    environment['python_version'] = python_version
    matches = []
    for requirement_string in dependencies.cloud_dependencies['openstack']:
        requirement = Requirement(requirement_string)
        if (requirement.name == 'openstacksdk' and
            (requirement.marker is None or
             requirement.marker.evaluate(environment))):
            matches.append(requirement)
    assert len(matches) == 1
    return matches[0]


def test_openstacksdk_dependency_keeps_python39_on_compatible_release_line():
    requirement = _openstacksdk_requirement('3.9')

    assert Version('4.1.0') in requirement.specifier
    assert Version('4.5.0') in requirement.specifier
    assert Version('4.6.0') not in requirement.specifier


def test_openstacksdk_dependency_keeps_python310_on_compatible_release_line():
    requirement = _openstacksdk_requirement('3.10')

    assert Version('4.13.0') in requirement.specifier
    assert Version('4.14.0') not in requirement.specifier


def test_openstacksdk_dependency_supports_current_python_versions():
    for python_version in ('3.11', '3.12', '3.13'):
        requirement = _openstacksdk_requirement(python_version)
        assert Version('4.17.0') in requirement.specifier


def test_openstack_is_included_in_all_cloud_dependencies():
    assert 'openstack' in dependencies.clouds_for_all
