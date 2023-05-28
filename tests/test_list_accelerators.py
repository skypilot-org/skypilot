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
    result = sky.list_accelerators(gpus_only=False,
                                   clouds="aws",
                                   region_filter='us-west-1')
    all_regions = []
    for res in result.values():
        for instance in res:
            all_regions.append(instance.region)
    assert all([region == 'us-west-1' for region in all_regions])


def test_list_accelerators_name_quantity_filter():
    result = sky.list_accelerators(name_filter='V100', quantity_filter=4)
    all_accelerators = []
    for res in result.values():
        for instance in res:
            all_accelerators.append(
                (instance.accelerator_name, instance.accelerator_count))
    assert all([
        item[0].__contains__('V100') and item[1] == 4
        for item in all_accelerators
    ])


def test_list_accelerators_positive_quantity_filter():
    result = sky.list_accelerators(quantity_filter=4)
    all_accelerators = []
    for res in result.values():
        for instance in res:
            all_accelerators.append(instance.accelerator_count)
    assert all(quantity == 4 for quantity in all_accelerators)


def test_list_accelerators_name_Lambda_filter():
    result = sky.list_accelerators(clouds='lambda', name_filter='V100')
    all_accelerators = []
    for res in result.values():
        for instance in res:
            all_accelerators.append((instance.accelerator_name, instance.cloud))
    assert all([
        item[0].__contains__('V100') and item[1] == 'Lambda'
        for item in all_accelerators
    ])


def test_list_accelerators_name_quantity_Lambda_filter():
    result = sky.list_accelerators(clouds='lambda',
                                   name_filter='A100',
                                   quantity_filter=4)
    all_accelerators = []
    for res in result.values():
        for instance in res:
            all_accelerators.append((instance.accelerator_name, instance.cloud,
                                     instance.accelerator_count))
    assert all([
        item[0].__contains__('A100') and item[1] == 'Lambda' and item[2] == 4
        for item in all_accelerators
    ])