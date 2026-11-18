"""Tests for the PEP 702 deprecation helpers in common_utils."""
import warnings

import pytest

from sky.utils import common_utils


def _add(a, b=1):
    return a + b


def test_deprecated_function_warns_and_calls_through():
    old_add = common_utils.deprecated_function(_add,
                                               name='add',
                                               deprecated_name='old_add',
                                               removing_version='1.0.0')
    with pytest.warns(common_utils.SkyPilotDeprecationWarning,
                      match='old_add.*removed in 1.0.0.*add\\(\\)'):
        assert old_add(1, b=2) == 3
    assert old_add.__name__ == '_add'


def test_deprecated_function_override_argument_in_message():
    old_add = common_utils.deprecated_function(_add,
                                               name='add',
                                               deprecated_name='old_add',
                                               removing_version='1.0.0',
                                               override_argument={'b': 2})
    with pytest.warns(common_utils.SkyPilotDeprecationWarning,
                      match='add\\(b=2\\)'):
        old_add(1)


def test_deprecated_on_function_sets_dunder_deprecated():

    @common_utils.deprecated('Use new_fn instead.')
    def old_fn():
        return 42

    assert old_fn.__deprecated__ == 'Use new_fn instead.'
    with pytest.warns(common_utils.SkyPilotDeprecationWarning,
                      match='Use new_fn instead.'):
        assert old_fn() == 42


def test_deprecated_on_class_and_method():

    @common_utils.deprecated('Use NewClass instead.')
    class OldClass:

        def __init__(self, x):
            self.x = x

    class Holder:

        @common_utils.deprecated('Use new_method instead.')
        def old_method(self):
            return 'ok'

    with pytest.warns(common_utils.SkyPilotDeprecationWarning,
                      match='Use NewClass instead.'):
        assert OldClass(3).x == 3
    with pytest.warns(common_utils.SkyPilotDeprecationWarning,
                      match='Use new_method instead.'):
        assert Holder().old_method() == 'ok'


def test_warning_is_shown_by_default_and_once_per_call_site():
    assert issubclass(common_utils.SkyPilotDeprecationWarning, FutureWarning)

    @common_utils.deprecated('Use new_fn instead.')
    def old_fn():
        return 1

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('default')
        for _ in range(3):
            old_fn()
    assert len(caught) == 1
