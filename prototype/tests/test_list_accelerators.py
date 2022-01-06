import sky


def test_list_accelerators():
    result = sky.list_accelerators()
    assert 'V100' in result, result
    assert 'tpu-v3-8' in result, result
    assert 'Inferentia' not in result, result


def test_list_ccelerators_all():
    result = sky.list_accelerators(gpus_only=False)
    assert 'V100' in result, result
    assert 'tpu-v3-8' in result, result
    assert 'Inferentia' in result, result


def test_list_accelerators_filters():
    result = sky.list_accelerators(gpus_only=False, name_filter='V100')
    assert sorted(result.keys()) == ['V100', 'V100-32GB'], result
