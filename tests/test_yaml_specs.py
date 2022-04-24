import pytest
import tempfile
import textwrap

import sky

def test_spot_nonexist_strategy():
    """Test the nonexist recovery strategy."""
    task_yaml = textwrap.dedent("""\
        resources:
            cloud: aws
            use_spot: true
            spot_recovery: nonexist""")
    with tempfile.NamedTemporaryFile(mode='w') as f:
        f.write(task_yaml)
        f.flush()
        with pytest.raises(
                ValueError,
                match='is not supported. The strategy should be among'):
            sky.Task.from_yaml(f.name)
