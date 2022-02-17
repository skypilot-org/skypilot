import pytest
import time

import sky


def test_nonexist_local_source():
    with pytest.raises(ValueError) as e:
        sky.Storage('hi', source=f'/tmp/test-{int(time.time())}')
    assert 'Local source path does not exist' in str(e)
