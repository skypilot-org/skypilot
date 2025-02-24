import sky

CLOUDS_TO_TEST = [
    'AWS', 'GCP', 'IBM', 'Azure', 'Lambda', 'OCI', 'scp', 'vsphere', 'nebius'
]


def test_list_accelerators():
    result = sky.list_accelerators()
    assert 'V100' in result, result
    assert 'tpu-v3' in result, result
    assert 'Inferentia' not in result, result
    assert 'Trainium' not in result, result
    assert 'A100-80GB' in result, result


def test_list_accelerators_all():
    result = sky.list_accelerators(gpus_only=False)
    assert 'V100' in result, result
    assert 'tpu-v3' in result, result
    assert 'Inferentia' in result, result
    assert 'Trainium' in result, result
    assert 'A100-80GB' in result, result


def test_list_accelerators_name_filter():
    """Test only accelerators with specified name are returned."""
    result = sky.list_accelerators(gpus_only=False, name_filter='V100')
    assert sorted(result.keys()) == ['V100', 'V100-32GB'], result


def test_list_accelerators_region_filter():
    """Test only accelerators in the specified cloud and region are returned."""
    result = sky.list_accelerators(gpus_only=False,
                                   clouds='aws',
                                   region_filter='us-west-1')
    all_regions = []
    for res in result.values():
        for instance in res:
            all_regions.append(instance.region)
    assert all([region == 'us-west-1' for region in all_regions])


def test_list_accelerators_name_quantity_filter():
    """Test only accelerators with specified name and quantity are returned."""
    result = sky.list_accelerators(name_filter='V100', quantity_filter=4)
    all_accelerators = []
    for res in result.values():
        for instance in res:
            all_accelerators.append(
                (instance.accelerator_name, instance.accelerator_count))
    assert all([
        name.__contains__('V100') and quantity == 4
        for name, quantity in all_accelerators
    ])


def test_list_accelerators_positive_quantity_filter():
    """Test only accelerators with specified quantity are returned."""
    result = sky.list_accelerators(quantity_filter=4)
    all_accelerators = []
    for res in result.values():
        for instance in res:
            all_accelerators.append(instance.accelerator_count)
    assert all(quantity == 4 for quantity in all_accelerators)


def test_list_accelerators_name_clouds_filter():
    """Test only accelerators in specified cloud, with specified name are returned."""
    for cloud in CLOUDS_TO_TEST:
        result = sky.list_accelerators(clouds=cloud.lower(), name_filter='V100')
        all_accelerators = []
        for res in result.values():
            for instance in res:
                all_accelerators.append(
                    (instance.accelerator_name, instance.cloud))
        assert all([
            name.__contains__('V100') and acc_cloud == cloud
            for name, acc_cloud in all_accelerators
        ])


def test_list_accelerators_name_quantity_clouds_filter():
    """Test only accelerators in specified cloud, with specified name and quantity are returned."""
    for cloud in CLOUDS_TO_TEST:
        result = sky.list_accelerators(clouds=cloud.lower(),
                                       name_filter='A100',
                                       quantity_filter=4)
        all_accelerators = []
        for res in result.values():
            for instance in res:
                all_accelerators.append(
                    (instance.accelerator_name, instance.cloud,
                     instance.accelerator_count))
        assert all([
            name.__contains__('A100') and acc_cloud == cloud and quantity == 4
            for name, acc_cloud, quantity in all_accelerators
        ])
