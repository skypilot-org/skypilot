import sky


def test_list_accelerators():
    result = sky.list_accelerators()
    assert 'V100' in result, result
    assert 'tpu-v3-8' in result, result
    assert 'Inferentia' not in result, result
    assert 'A100-80GB' in result, result


def test_list_ccelerators_all():
    result = sky.list_accelerators(gpus_only=False)
    assert 'V100' in result, result
    assert 'tpu-v3-8' in result, result
    assert 'Inferentia' in result, result
    assert 'A100-80GB' in result, result


def test_list_accelerators_name_filter():
    result = sky.list_accelerators(gpus_only=False, name_filter='V100')
    assert sorted(result.keys()) == ['V100', 'V100-32GB'], result

def test_list_accelerators_region_filter():
    result = sky.list_accelerators(gpus_only=False, clouds="aws", region_filter='us-west-1')
    all_regions = []
    for res in result.values():
        for instance in res:
            all_regions.append(instance.region)
    assert all([region == 'us-west-1' for region in all_regions])
