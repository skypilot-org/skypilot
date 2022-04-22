import tempfile
import textwrap

import pytest

from sky import spot


def test_spot_controller():
    """Test the spot yaml."""
    controller = spot.SpotController('test-spot-controller', 'examples/spot_recovery.yaml')
    controller.start()

def test_spot_nonexist_strategy():
    """Test the nonexist recovery strategy."""
    task_yaml = textwrap.dedent("""\
        resources:
            cloud: aws
            use_spot: true
            spot_recovery: nonexist""")
    with tempfile.NamedTemporaryFile() as f:
        f.write(task_yaml)
        f.flush()
        with pytest.raises(ValueError, match='is not supported. The strategy should be among'):
            controller = spot.SpotController('test-spot-nonexist-strategy', f.name)
            controller.start()