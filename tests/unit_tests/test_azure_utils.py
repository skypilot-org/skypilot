"""Tests for Azure utilities and provisioning config."""
from unittest import mock

import pytest

from sky import clouds
from sky import exceptions
from sky.adaptors import azure
from sky.clouds.utils import azure_utils
from sky.provision.azure.config import _remove_msi_resources_from_template
from sky.provision.azure.config import _remove_network_resources_from_template
from sky.provision.azure.config import _resolve_custom_managed_identity

_SIG_IMAGE_ID = ('/subscriptions/sub-123/resourceGroups/my-rg/providers/'
                 'Microsoft.Compute/galleries/my-gallery/images/my-image/'
                 'versions/1.0.3')


def test_validate_image_id():
    # Valid marketplace image ID
    azure_utils.validate_image_id('publisher:offer:sku:version')

    # Valid community image ID
    azure_utils.validate_image_id(
        '/CommunityGalleries/gallery-name/Images/image-name')

    # Valid private Shared Image Gallery image-version resource ID
    azure_utils.validate_image_id(_SIG_IMAGE_ID)

    # Invalid format (neither marketplace nor community)
    with pytest.raises(ValueError):
        azure_utils.validate_image_id(
            'CommunityGalleries/gallery-name/Images/image-name')

    # Invalid marketplace image ID (too few parts)
    with pytest.raises(ValueError):
        azure_utils.validate_image_id('publisher:offer:sku')

    # A resource ID missing the version segment is not a valid SIG image ID.
    with pytest.raises(ValueError):
        azure_utils.validate_image_id(
            '/subscriptions/sub-123/resourceGroups/my-rg/providers/'
            'Microsoft.Compute/galleries/my-gallery/images/my-image')


def test_parse_shared_image_gallery_id():
    parsed = azure_utils.parse_shared_image_gallery_id(_SIG_IMAGE_ID)
    assert parsed == {
        'subscription_id': 'sub-123',
        'resource_group': 'my-rg',
        'gallery_name': 'my-gallery',
        'image_name': 'my-image',
        'version': '1.0.3',
    }

    # Azure resource IDs are case-insensitive in their literal segments.
    assert azure_utils.parse_shared_image_gallery_id(
        _SIG_IMAGE_ID.replace('resourceGroups', 'resourcegroups')) is not None

    # Non-SIG image IDs return None rather than raising.
    assert azure_utils.parse_shared_image_gallery_id(
        'publisher:offer:sku:version') is None
    assert azure_utils.parse_shared_image_gallery_id(
        '/CommunityGalleries/gallery-name/Images/image-name') is None

    # A gallery image *definition* (no version segment) is not a bootable
    # image and must not be treated as one.
    assert azure_utils.parse_shared_image_gallery_id(
        '/subscriptions/sub-123/resourceGroups/my-rg/providers/'
        'Microsoft.Compute/galleries/my-gallery/images/my-image') is None

    # An unrelated resource ID (managed identity) returns None.
    assert azure_utils.parse_shared_image_gallery_id(
        '/subscriptions/sub-123/resourceGroups/my-rg/providers/'
        'Microsoft.ManagedIdentity/userAssignedIdentities/my-msi') is None

    # A syntactically valid ARM id that matches the gallery image-version path
    # but omits the resource-group scope must return None (not raise KeyError).
    assert azure_utils.parse_shared_image_gallery_id(
        '/subscriptions/sub-123/providers/Microsoft.Compute/galleries/'
        'my-gallery/images/my-image/versions/1.0.3') is None


def _mock_compute_client(size_in_gb):
    client = mock.MagicMock()
    version = mock.MagicMock()
    version.storage_profile.os_disk_image.size_in_gb = size_in_gb
    client.gallery_image_versions.get.return_value = version
    return client


def test_get_shared_image_gallery_image_size():
    client = _mock_compute_client(size_in_gb=64)
    assert azure_utils.get_shared_image_gallery_image_size(
        client, 'my-rg', 'my-gallery', 'my-image', '1.0.3') == 64.0
    client.gallery_image_versions.get.assert_called_once_with(
        resource_group_name='my-rg',
        gallery_name='my-gallery',
        gallery_image_name='my-image',
        gallery_image_version_name='1.0.3')


def test_get_shared_image_gallery_image_size_missing_size():
    client = _mock_compute_client(size_in_gb=None)
    with pytest.raises(exceptions.ResourcesUnavailableError):
        azure_utils.get_shared_image_gallery_image_size(client, 'my-rg',
                                                        'my-gallery',
                                                        'my-image', '1.0.3')


def test_get_shared_image_gallery_image_size_missing_storage_profile():
    client = mock.MagicMock()
    version = mock.MagicMock()
    version.storage_profile = None
    client.gallery_image_versions.get.return_value = version
    with pytest.raises(exceptions.ResourcesUnavailableError):
        azure_utils.get_shared_image_gallery_image_size(client, 'my-rg',
                                                        'my-gallery',
                                                        'my-image', '1.0.3')


def test_get_shared_image_gallery_image_size_azure_error():
    client = mock.MagicMock()
    client.gallery_image_versions.get.side_effect = azure.exceptions(
    ).AzureError('boom')
    with pytest.raises(exceptions.ResourcesUnavailableError):
        azure_utils.get_shared_image_gallery_image_size(client, 'my-rg',
                                                        'my-gallery',
                                                        'my-image', '1.0.3')


def test_azure_get_image_size_shared_gallery_uses_image_subscription():
    # The compute client must target the image's own subscription, which may
    # differ from the active project.
    with mock.patch.object(azure, 'get_client') as mock_get_client, \
            mock.patch.object(azure_utils,
                              'get_shared_image_gallery_image_size',
                              return_value=64.0) as mock_size:
        size = clouds.Azure.get_image_size(_SIG_IMAGE_ID, region=None)
    assert size == 64.0
    mock_get_client.assert_called_once_with('compute', 'sub-123')
    assert mock_size.call_args.args[1:] == ('my-rg', 'my-gallery', 'my-image',
                                            '1.0.3')


def test_azure_get_image_size_shared_gallery_falls_back_on_no_access():
    # If the image's subscription is not readable, fall back to 0.0 instead of
    # failing the launch.
    with mock.patch.object(azure, 'get_client'), \
            mock.patch.object(
                azure_utils, 'get_shared_image_gallery_image_size',
                side_effect=exceptions.ResourcesUnavailableError('no access')):
        size = clouds.Azure.get_image_size(_SIG_IMAGE_ID, region=None)
    assert size == 0.0


class TestResolveCustomManagedIdentity:
    """Tests for _resolve_custom_managed_identity."""

    def test_none_remote_identity(self):
        """Returns None when remote_identity is None."""
        assert _resolve_custom_managed_identity(None, 'sub-id', 'rg') is None

    def test_local_credentials(self):
        """Returns None for LOCAL_CREDENTIALS."""
        assert _resolve_custom_managed_identity('LOCAL_CREDENTIALS', 'sub-id',
                                                'rg') is None

    def test_service_account(self):
        """Returns None for SERVICE_ACCOUNT."""
        assert _resolve_custom_managed_identity('SERVICE_ACCOUNT', 'sub-id',
                                                'rg') is None

    def test_no_upload(self):
        """Returns None for NO_UPLOAD."""
        assert _resolve_custom_managed_identity('NO_UPLOAD', 'sub-id',
                                                'rg') is None

    def test_custom_msi_name(self):
        """Custom MSI name is resolved to full resource ID."""
        result = _resolve_custom_managed_identity('my-custom-msi', 'sub-123',
                                                  'my-rg')
        expected = ('/subscriptions/sub-123'
                    '/resourceGroups/my-rg'
                    '/providers/Microsoft.ManagedIdentity'
                    '/userAssignedIdentities/my-custom-msi')
        assert result == expected

    def test_full_resource_id(self):
        """Full resource ID is used directly."""
        full_id = ('/subscriptions/sub-456/resourceGroups/other-rg'
                   '/providers/Microsoft.ManagedIdentity'
                   '/userAssignedIdentities/existing-msi')
        result = _resolve_custom_managed_identity(full_id, 'sub-123', 'my-rg')
        assert result == full_id


class TestRemoveMsiResourcesFromTemplate:
    """Tests for _remove_msi_resources_from_template."""

    def test_removes_msi_and_role_assignment(self):
        """MSI and role assignment resources are removed."""
        template = _make_arm_template()
        _remove_msi_resources_from_template(template)
        resource_types = [r['type'] for r in template['resources']]
        assert 'Microsoft.ManagedIdentity/userAssignedIdentities' \
            not in resource_types
        assert 'Microsoft.Authorization/roleAssignments' \
            not in resource_types
        assert 'Microsoft.Network/networkSecurityGroups' in resource_types
        assert 'Microsoft.Network/virtualNetworks' in resource_types
        assert 'msi' not in template['outputs']
        assert 'subnet' in template['outputs']
        assert 'nsg' in template['outputs']


class TestRemoveNetworkResourcesFromTemplate:
    """Tests for _remove_network_resources_from_template."""

    def test_removes_vnet(self):
        """VNet resource is removed, NSG is kept."""
        template = _make_arm_template()
        _remove_network_resources_from_template(template)
        resource_types = [r['type'] for r in template['resources']]
        assert 'Microsoft.Network/virtualNetworks' not in resource_types
        assert 'Microsoft.Network/networkSecurityGroups' in resource_types
        assert 'Microsoft.ManagedIdentity/userAssignedIdentities' \
            in resource_types
        assert 'subnet' not in template['outputs']
        assert 'nsg' in template['outputs']
        assert 'msi' in template['outputs']


def _make_arm_template():
    """Create a minimal ARM template for testing."""
    return {
        'resources': [
            {
                'type': 'Microsoft.ManagedIdentity/userAssignedIdentities'
            },
            {
                'type': 'Microsoft.Authorization/roleAssignments'
            },
            {
                'type': 'Microsoft.Network/networkSecurityGroups'
            },
            {
                'type': 'Microsoft.Network/virtualNetworks'
            },
        ],
        'outputs': {
            'subnet': {
                'type': 'string',
                'value': 'subnet-id'
            },
            'nsg': {
                'type': 'string',
                'value': 'nsg-id'
            },
            'msi': {
                'type': 'string',
                'value': 'msi-id'
            },
        }
    }


class TestCheckQuotaAvailable:
    """Tests for azure_utils.check_quota_available."""

    @pytest.fixture(autouse=True)
    def _clear_snapshots(self):
        azure_utils._quota_snapshots.clear()
        yield
        azure_utils._quota_snapshots.clear()

    def _capacity(self,
                  family_headroom,
                  total=10_000,
                  restricted=frozenset(),
                  family_by_sku=None):
        return azure_utils.RegionQuotaCapacity(family_headroom=family_headroom,
                                               total_vcpu_headroom=total,
                                               restricted_skus=restricted,
                                               family_by_sku=family_by_sku or
                                               {})

    def test_restricted_sku_is_conclusively_unavailable(self, monkeypatch):
        capacity = self._capacity(
            family_headroom={'standardhbv3family': 3540},
            restricted=frozenset({'Standard_HB120rs_v3'}),
            family_by_sku={'Standard_HB120rs_v3': 'standardHBv3Family'})
        monkeypatch.setattr(azure_utils, '_fetch_region_quota_capacity',
                            lambda region: capacity)
        assert not azure_utils.check_quota_available('Standard_HB120rs_v3',
                                                     'southcentralus', False)

    def test_insufficient_family_headroom_blocks(self, monkeypatch):
        capacity = self._capacity(
            family_headroom={'standardncadsh100v5family': 20},
            family_by_sku={
                'Standard_NC40ads_H100_v5': 'StandardNCadsH100v5Family'
            })
        monkeypatch.setattr(azure_utils, '_fetch_region_quota_capacity',
                            lambda region: capacity)
        monkeypatch.setattr(azure_utils, '_instance_type_vcpus',
                            lambda instance_type: 40)
        assert not azure_utils.check_quota_available('Standard_NC40ads_H100_v5',
                                                     'australiaeast', False)

    def test_sufficient_headroom_allows(self, monkeypatch):
        capacity = self._capacity(
            family_headroom={'standardhbv3family': 3540},
            family_by_sku={'Standard_HB120rs_v3': 'standardHBv3Family'})
        monkeypatch.setattr(azure_utils, '_fetch_region_quota_capacity',
                            lambda region: capacity)
        monkeypatch.setattr(azure_utils, '_instance_type_vcpus',
                            lambda instance_type: 120)
        assert azure_utils.check_quota_available('Standard_HB120rs_v3',
                                                 'southcentralus', False)

    def test_total_regional_vcpu_headroom_blocks(self, monkeypatch):
        # Family quota alone is not enough; the regional total gates too.
        capacity = self._capacity(
            family_headroom={'standardhbv3family': 3540},
            total=100,
            family_by_sku={'Standard_HB120rs_v3': 'standardHBv3Family'})
        monkeypatch.setattr(azure_utils, '_fetch_region_quota_capacity',
                            lambda region: capacity)
        monkeypatch.setattr(azure_utils, '_instance_type_vcpus',
                            lambda instance_type: 120)
        assert not azure_utils.check_quota_available('Standard_HB120rs_v3',
                                                     'southcentralus', False)

    def test_sku_not_sold_in_region_blocks(self, monkeypatch):
        capacity = self._capacity(family_headroom={})
        monkeypatch.setattr(azure_utils, '_fetch_region_quota_capacity',
                            lambda region: capacity)
        assert not azure_utils.check_quota_available('Standard_HB120rs_v3',
                                                     'koreacentral', False)

    def test_probe_failure_propagates_to_the_caller(self, monkeypatch):
        # The call site (cloud_vm_ray_backend) catches, logs, and treats
        # the check as inconclusive; swallowing here would hide the error.
        def boom(region):
            raise RuntimeError('SDK exploded')

        monkeypatch.setattr(azure_utils, '_fetch_region_quota_capacity', boom)
        with pytest.raises(RuntimeError, match='SDK exploded'):
            azure_utils.check_quota_available('Standard_HB120rs_v3',
                                              'southcentralus', False)

    def test_spot_gates_on_low_priority_bucket(self, monkeypatch):
        capacity = self._capacity(
            family_headroom={
                'standardhbv3family': 3540,
                'lowprioritycores': 8,
            },
            family_by_sku={'Standard_HB120rs_v3': 'standardHBv3Family'})
        monkeypatch.setattr(azure_utils, '_fetch_region_quota_capacity',
                            lambda region: capacity)
        monkeypatch.setattr(azure_utils, '_instance_type_vcpus',
                            lambda instance_type: 120)
        assert not azure_utils.check_quota_available('Standard_HB120rs_v3',
                                                     'southcentralus', True)
        assert azure_utils.check_quota_available('Standard_HB120rs_v3',
                                                 'southcentralus', False)

    def test_unknown_instance_size_blocks_only_on_zero_quota(self, monkeypatch):
        # An unsized SKU degrades to a nonzero-quota check (needed=1).
        capacity = self._capacity(
            family_headroom={'standardhbv3family': 0},
            family_by_sku={'Standard_HB120rs_v3': 'standardHBv3Family'})
        monkeypatch.setattr(azure_utils, '_fetch_region_quota_capacity',
                            lambda region: capacity)
        monkeypatch.setattr(azure_utils, '_instance_type_vcpus',
                            lambda instance_type: 0)
        assert not azure_utils.check_quota_available('Standard_HB120rs_v3',
                                                     'southcentralus', False)

    def test_snapshot_is_ttl_cached_per_region(self, monkeypatch):
        calls = []

        def fake_fetch(region):
            calls.append(region)
            return self._capacity(
                family_headroom={'standardhbv3family': 3540},
                family_by_sku={'Standard_HB120rs_v3': 'standardHBv3Family'})

        monkeypatch.setattr(azure_utils, '_fetch_region_quota_capacity',
                            fake_fetch)
        monkeypatch.setattr(azure_utils, '_instance_type_vcpus',
                            lambda instance_type: 120)
        for _ in range(3):
            azure_utils.check_quota_available('Standard_HB120rs_v3',
                                              'southcentralus', False)
        azure_utils.check_quota_available('Standard_HB120rs_v3', 'eastus',
                                          False)
        assert calls == ['southcentralus', 'eastus']

        # A snapshot older than the TTL is refetched.
        snapshot = azure_utils._quota_snapshots['southcentralus']
        azure_utils._quota_snapshots['southcentralus'] = snapshot._replace(
            fetched_at=snapshot.fetched_at -
            azure_utils._QUOTA_SNAPSHOT_TTL_SECONDS - 1)
        azure_utils.check_quota_available('Standard_HB120rs_v3',
                                          'southcentralus', False)
        assert calls == ['southcentralus', 'eastus', 'southcentralus']

    def test_stale_no_headroom_is_reverified_before_blocking(self, monkeypatch):
        # Quota frees up when clusters come down; a cached "no headroom"
        # older than the block window must not condemn the region without
        # a fresh reading.
        capacities = iter([
            self._capacity(
                family_headroom={'standardhbv3family': 0},
                family_by_sku={'Standard_HB120rs_v3': 'standardHBv3Family'}),
            self._capacity(
                family_headroom={'standardhbv3family': 3540},
                family_by_sku={'Standard_HB120rs_v3': 'standardHBv3Family'}),
        ])
        calls = []

        def fake_fetch(region):
            calls.append(region)
            return next(capacities)

        monkeypatch.setattr(azure_utils, '_fetch_region_quota_capacity',
                            fake_fetch)
        monkeypatch.setattr(azure_utils, '_instance_type_vcpus',
                            lambda instance_type: 120)
        # Fresh fetch: the zero-headroom verdict is conclusive.
        assert not azure_utils.check_quota_available('Standard_HB120rs_v3',
                                                     'southcentralus', False)
        # Age the snapshot past the block window (still within the TTL):
        # the stale "no" triggers a re-verify, which now sees headroom.
        snapshot = azure_utils._quota_snapshots['southcentralus']
        azure_utils._quota_snapshots['southcentralus'] = snapshot._replace(
            fetched_at=snapshot.fetched_at -
            azure_utils._QUOTA_BLOCK_MAX_AGE_SECONDS - 1)
        assert azure_utils.check_quota_available('Standard_HB120rs_v3',
                                                 'southcentralus', False)
        assert calls == ['southcentralus', 'southcentralus']

    def test_recent_no_headroom_blocks_without_refetch(self, monkeypatch):
        calls = []

        def fake_fetch(region):
            calls.append(region)
            return self._capacity(
                family_headroom={'standardhbv3family': 0},
                family_by_sku={'Standard_HB120rs_v3': 'standardHBv3Family'})

        monkeypatch.setattr(azure_utils, '_fetch_region_quota_capacity',
                            fake_fetch)
        monkeypatch.setattr(azure_utils, '_instance_type_vcpus',
                            lambda instance_type: 120)
        # Within the block window a cached "no" is trusted, so a
        # multi-SKU failover does not refetch per blocked check.
        for _ in range(3):
            assert not azure_utils.check_quota_available(
                'Standard_HB120rs_v3', 'southcentralus', False)
        assert calls == ['southcentralus']

    def test_usage_family_names_are_normalized(self):
        # The Usage API emits family names with stray spaces and mixed
        # casing; matching against Resource SKUs families must survive it.
        assert azure_utils._normalize_family(
            'standard NDASv4_A100 Family') == 'standardndasv4_a100family'
        assert azure_utils._normalize_family(
            'StandardHBv3Family') == 'standardhbv3family'

    def test_empty_family_name_skips_family_gate_not_the_total_gate(
            self, monkeypatch):
        # A SKU sold in the region whose family name the API omits is NOT
        # a conclusive no: the family gate is skipped, but the
        # regional-total gate still applies.
        monkeypatch.setattr(azure_utils, '_instance_type_vcpus',
                            lambda instance_type: 120)
        roomy = self._capacity(family_headroom={},
                               total=10_000,
                               family_by_sku={'Standard_HB120rs_v3': ''})
        monkeypatch.setattr(azure_utils, '_fetch_region_quota_capacity',
                            lambda region: roomy)
        assert azure_utils.check_quota_available('Standard_HB120rs_v3',
                                                 'southcentralus', False)
        azure_utils._quota_snapshots.clear()
        cramped = self._capacity(family_headroom={},
                                 total=8,
                                 family_by_sku={'Standard_HB120rs_v3': ''})
        monkeypatch.setattr(azure_utils, '_fetch_region_quota_capacity',
                            lambda region: cramped)
        assert not azure_utils.check_quota_available('Standard_HB120rs_v3',
                                                     'southcentralus', False)


class _FakeUsageName:

    def __init__(self, value):
        self.value = value


class _FakeUsage:

    def __init__(self, name, limit, current_value):
        self.name = _FakeUsageName(name)
        self.limit = limit
        self.current_value = current_value


class _FakeRestriction:

    def __init__(self, reason_code, restriction_type):
        self.reason_code = reason_code
        self.type = restriction_type


class _FakeSku:

    def __init__(self, name, family, restrictions=()):
        self.resource_type = 'virtualMachines'
        self.name = name
        self.family = family
        self.restrictions = list(restrictions)


class _FakeComputeClient:

    def __init__(self, usages, skus):
        self.usage = self
        self.resource_skus = self
        self._usages = usages
        self._skus = skus

    def list(self, *args, **kwargs):
        # Dispatched by the argument shape: usage.list(region) is
        # positional, resource_skus.list(filter=...) is keyword-only.
        if 'filter' in kwargs:
            return list(self._skus)
        return list(self._usages)


class TestFetchRegionQuotaCapacity:
    """Tests for the Azure API response parsing."""

    def test_malformed_rows_are_tolerated_and_restrictions_parse(
            self, monkeypatch):
        usages = [
            # Float-string limits parse; None and garbage limits are
            # skipped rather than failing the whole region fetch.
            _FakeUsage('standardHBv3Family', '3600.0', '60.0'),
            _FakeUsage('brokenFamily', None, 5),
            _FakeUsage('alsoBroken', 'not-a-number', 5),
            _FakeUsage('cores', 10_000, 120),
        ]
        skus = [
            _FakeSku('Standard_HB120rs_v3', 'standardHBv3Family'),
            _FakeSku('Standard_ND96asr_v4',
                     'standardNDASv4_A100Family',
                     restrictions=[
                         _FakeRestriction('NotAvailableForSubscription',
                                          'Location'),
                     ]),
            # Zone-scoped restrictions do not restrict the region.
            _FakeSku('Standard_NC24ads_A100_v4',
                     'standardNCADSA100v4Family',
                     restrictions=[
                         _FakeRestriction('NotAvailableForSubscription',
                                          'Zone'),
                     ]),
        ]
        monkeypatch.setattr(
            azure_utils.azure, 'get_client',
            lambda name, subscription_id: _FakeComputeClient(usages, skus))
        monkeypatch.setattr(azure_utils.azure, 'get_subscription_id',
                            lambda: 'sub-id')

        capacity = azure_utils._fetch_region_quota_capacity('southcentralus')

        assert capacity.family_headroom == {'standardhbv3family': 3540}
        assert capacity.total_vcpu_headroom == 9880
        assert capacity.restricted_skus == frozenset({'Standard_ND96asr_v4'})
        assert capacity.family_by_sku['Standard_HB120rs_v3'] == (
            'standardHBv3Family')
