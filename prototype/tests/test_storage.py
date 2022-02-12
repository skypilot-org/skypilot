import pytest
import time

import sky


def test_nonexist_local_source():
    with pytest.raises(ValueError) as e:
        sky.Storage('hi', source=f'/tmp/test-{int(time.time())}')
    assert 'Local source path does not exist' in str(e)


def test_nonexist_cloud_source():
    for scheme in ['s3', 'gs']:
        with pytest.raises(ValueError) as e:
            name = f'test-{int(time.time())}'
            sky.Storage(name, source=f'{scheme}://{name}')
        assert ('Attempted to connect to a non-existent bucket: '
                f'{scheme}://{name}') in str(e)
