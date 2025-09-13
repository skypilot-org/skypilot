from unittest.mock import patch

import pytest

from sky import resources as resources_lib
from sky import task as task_lib
from sky.optimizer import _fill_in_launchable_resources


def test_resources_validate_with_cloud(monkeypatch):
    """Test that when _fill_in_launchable_resources calls Resources.validate(), 
    the cloud is set and is not None."""

    # Create a task with resources that don't have a cloud set initially
    task = task_lib.Task(name='test')
    resources = resources_lib.Resources(accelerators='V100:1')
    task.set_resources(resources)

    # Track calls to validate and check that cloud is set when validate called.
    original_validate = resources_lib.Resources.validate
    cloud_values_during_validate = []

    def mock_validate(resources_instance):
        # Capture the cloud value when validate is called
        cloud_values_during_validate.append(resources_instance.cloud)

        # Assert cloud is not None BEFORE calling original validate
        assert resources_instance.cloud is not None, f"Cloud was None \
            when validate() was called"

        # Call the original validate method
        return original_validate(resources_instance)

    # Patch the validate method to intercept calls and check cloud is set
    with patch.object(resources_lib.Resources, 'validate', new=mock_validate):
        _fill_in_launchable_resources(task, blocked_resources=None, quiet=True)

        # Assert that validate was called with cloud set (not None)
        assert len(cloud_values_during_validate) > 0, "validate() was \
            not called"

        for cloud_value in cloud_values_during_validate:
            assert cloud_value is not None, f"Cloud was None when validate() \
                was called"
