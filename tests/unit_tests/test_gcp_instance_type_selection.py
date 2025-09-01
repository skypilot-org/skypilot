import sky


def test_v100_two_gpu_gets_n1_standard_8():
    """Ensure SkyPilot picks n1-standard-8 as the host VM for 2×V100 on GCP."""
    instance_types, fuzzy = sky.catalog.get_instance_type_for_accelerator(
        'V100', 2, clouds='gcp'
    )

    # The primary recommendation should be n1-standard-8.
    assert instance_types, 'No instance type returned for V100:2 on GCP.'
    assert 'n1-standard-8' in instance_types, (
        f"Expected 'n1-standard-8' in recommendations, got {instance_types}"
    )

    # Fuzzy list should be empty since we expect an exact match.
    assert not fuzzy, f'Unexpected fuzzy candidates returned: {fuzzy}' 