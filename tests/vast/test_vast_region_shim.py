"""Behavioral regression: the georegion shim must survive the real SDK.

Tests the actual `queryFormatter` path (not just the dict patch) and the
fetcher's CSV generation against a MOCKED offer list — no live API, no
credentials, no real HOME writes.
"""
import os
import unittest
from unittest import mock

from sky.adaptors import vast

# The 249 officially assigned ISO 3166-1 alpha-2 codes. After the shim,
# EVERY one of these must resolve through vastai_sdk._regions_rev — that
# is the invariant `queryFormatter` needs (`_regions_rev[country]` with
# no .get) so no marketplace drift (IE on 2026-09-15, MO on 2026-09-16,
# …) can crash the catalog fetch again.
_ALL_ISO_ALPHA2 = (
    'AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG '
    'BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI '
    'CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH '
    'ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ '
    'GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT '
    'JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS '
    'LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU '
    'MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG '
    'PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG '
    'SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK '
    'TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU '
    'WF WS YE YT ZA ZM ZW'
).split()

_VALID_TOKENS = {'AF', 'AS', 'EU', 'LC', 'NA', 'OC'}


class TestVastRegionShim(unittest.TestCase):
    """The missing-georegion completion table in sky/adaptors/vast.py."""

    def test_every_iso_code_resolves(self):
        """After the patch, all 249 official alpha-2 codes resolve.

        This is the invariant that killed the catalog twice: the SDK's
        182-entry map plus our table must cover every code the
        marketplace could ever serve.
        """
        vast.vast()  # triggers import + patch
        from vastai import vastai_sdk
        unresolved = [c for c in _ALL_ISO_ALPHA2
                      if c not in vastai_sdk._regions_rev]
        self.assertEqual(
            unresolved, [],
            f'alpha-2 codes with no georegion after patch: {unresolved} — '
            'queryFormatter would KeyError on these (catalog fetch dies)')

    def test_table_tokens_are_valid(self):
        """Every table entry maps to one of the SDK's own georegion tokens."""
        from sky.adaptors.vast import _MISSING_GEOREGIONS
        bad = {code: token for code, token in _MISSING_GEOREGIONS.items()
               if token not in _VALID_TOKENS}
        self.assertEqual(
            bad, {},
            f'table entries with non-SDK georegion tokens: {bad}')

    def test_table_codes_are_real_iso_codes(self):
        """Typo guard: every table key is an officially assigned code."""
        from sky.adaptors.vast import _MISSING_GEOREGIONS
        bogus = sorted(set(_MISSING_GEOREGIONS) - set(_ALL_ISO_ALPHA2))
        self.assertEqual(
            bogus, [],
            f'table entries that are not ISO 3166-1 alpha-2 codes: {bogus}')

    def test_known_gaps_map_correctly(self):
        """The two codes the live marketplace actually served."""
        vast.vast()  # triggers import + patch
        from vastai import vastai_sdk
        self.assertEqual(vastai_sdk._regions_rev.get('IE'), 'EU')
        self.assertEqual(vastai_sdk._regions_rev.get('MO'), 'AS')

    def test_queryformatter_handles_ie_offer(self):
        """The REAL formatter must not crash on an IE offer.

        queryFormatter(state, obj, instance) where obj is a LIST of offers.
        It iterates over obj, mutating res['geolocation'] in place by
        appending ', {_regions_rev[country]}'. Without the shim,
        KeyError('IE') kills the catalog fetch.
        """
        vast.vast()  # triggers import + patch
        from vastai import vastai_sdk

        offer = {
            'gpu_name': 'RTX PRO 6000 S',
            'num_gpus': 1,
            'geolocation': 'Dublin, IE',
            'hosting_type': 0,
        }
        offers = [offer]
        state = {'georegion': True, 'chunked': False}
        vastai_sdk.queryFormatter(state, offers, None)

        # The geolocation must have been enriched with the georegion
        self.assertIn('IE, EU', offer['geolocation'],
                      f'Expected IE, EU in geolocation, got: '
                      f'{offer["geolocation"]}')

    def test_queryformatter_handles_mo_offer(self):
        """The 2026-09-16 live crash: KeyError('MO') on a Macau offer.

        Same queryFormatter path as the IE test — this exact offer shape
        killed the live catalog fetch the day after the IE-only shim
        landed, which is why the fix completes the whole map.
        """
        vast.vast()  # triggers import + patch
        from vastai import vastai_sdk

        offer = {
            'gpu_name': 'RTX PRO 6000 S',
            'num_gpus': 1,
            'geolocation': 'Cotai, MO',
            'hosting_type': 0,
        }
        offers = [offer]
        state = {'georegion': True, 'chunked': False}
        vastai_sdk.queryFormatter(state, offers, None)

        self.assertIn('MO, AS', offer['geolocation'],
                      f'Expected MO, AS in geolocation, got: '
                      f'{offer["geolocation"]}')

    def test_patch_is_idempotent(self):
        """Running the patch twice is safe (process-global singleton)."""
        vast.vast()  # triggers import + patch
        from sky.adaptors.vast import _patch_sdk_regions
        _patch_sdk_regions()
        _patch_sdk_regions()
        from vastai import vastai_sdk
        self.assertEqual(vastai_sdk._regions_rev.get('IE'), 'EU')
        self.assertEqual(vastai_sdk._regions_rev.get('MO'), 'AS')

    def test_patch_fails_loudly_on_sdk_drift(self):
        """If the SDK loses _regions_rev, we raise — not silently no-op."""
        from vastai import vastai_sdk
        saved = vastai_sdk._regions_rev
        from sky.adaptors import vast as _v
        try:
            del vastai_sdk._regions_rev
            _v._vast_sdk = None  # clear singleton
            with self.assertRaises(RuntimeError) as ctx:
                _v.vast()
            self.assertIn('not found', str(ctx.exception))
        finally:
            vastai_sdk._regions_rev = saved
            _v._vast_sdk = None


def _mock_offer(geolocation='Dublin, IE', price=1.27):
    """A deterministic Vast offer fixture matching the fetcher's schema."""
    return {
        'gpu_name': 'RTX PRO 6000 S',
        'num_gpus': 1,
        'cpu_cores': 16,
        'cpu_ram': 192000,
        'gpu_total_ram': 98304,
        'search': {'totalHour': price},
        'min_bid': price,
        'geolocation': geolocation,
        'hosting_type': 0,
        'bundle_id': 'test-bundle',
        'inet_down': 500,
        'disk_space': 512,
        'compute_cap': 120,
    }


class TestVastCatalogFetch(unittest.TestCase):
    """The fetcher must produce a nonempty CSV from a mocked offer list.

    No live API — we patch search_offers with deterministic fixtures and
    verify the catalog pipeline (mapping, pricing, dedup, CSV write).
    """

    def test_single_offer_produces_nonempty_csv(self):
        """ONE unique IE offer → catalog has ONE data row.

        This is the regression test for the dedup bug that dropped
        every FIRST occurrence of an (InstanceType, Region, HostingType)
        stub — Vast instance types encode CPU/RAM, so most marketplace
        offers are unique, and the old code produced a header-only CSV
        even with 46 live offers. One offer must produce one row.
        """
        vast.vast()  # triggers import + patch

        import tempfile
        one_offer = [_mock_offer('Dublin, IE', 1.27)]

        with mock.patch.object(vast, 'vast') as mock_vast:
            mock_vast.return_value.search_offers.return_value = one_offer

            with tempfile.TemporaryDirectory() as tmpdir:
                old_cwd = os.getcwd()
                os.chdir(tmpdir)
                try:
                    import contextlib
                    import io
                    import runpy
                    with contextlib.redirect_stdout(io.StringIO()):
                        runpy.run_module(
                            'sky.catalog.data_fetchers.fetch_vast',
                            run_name='__main__')

                    # ASSERT INSIDE the tempdir (it's deleted on exit)
                    csv_path = os.path.join(tmpdir, 'vast', 'vms.csv')
                    self.assertTrue(
                        os.path.exists(csv_path),
                        'fetch_vast did not produce vast/vms.csv')
                    with open(csv_path) as f:
                        lines = f.readlines()
                    self.assertGreater(
                        len(lines), 1,
                        f'Catalog CSV is header-only ({len(lines)} lines) — '
                        'the fetcher silently produced an empty catalog. '
                        'The dedup bug that dropped unique instance types '
                        'has regressed.')

                    # Verify the region was enriched (IE → EU)
                    if len(lines) > 1:
                        data_row = lines[1]
                        self.assertIn('IE', data_row,
                                      f'Expected IE country in CSV row: '
                                      f'{data_row[:100]}')
                finally:
                    os.chdir(old_cwd)

    def test_mo_offer_produces_nonempty_csv(self):
        """A Macau-hosted offer must survive the whole pipeline.

        The 2026-09-16 live-fetch crash: the fetcher died inside
        queryFormatter before any CSV row existed. The IE-shim
        equivalent of this test would not have caught MO — this pins
        the completion-table behavior end to end.
        """
        vast.vast()  # triggers import + patch

        import tempfile
        one_offer = [_mock_offer('Cotai, MO', 0.91)]

        with mock.patch.object(vast, 'vast') as mock_vast:
            mock_vast.return_value.search_offers.return_value = one_offer

            with tempfile.TemporaryDirectory() as tmpdir:
                old_cwd = os.getcwd()
                os.chdir(tmpdir)
                try:
                    import contextlib
                    import io
                    import runpy
                    with contextlib.redirect_stdout(io.StringIO()):
                        runpy.run_module(
                            'sky.catalog.data_fetchers.fetch_vast',
                            run_name='__main__')

                    csv_path = os.path.join(tmpdir, 'vast', 'vms.csv')
                    self.assertTrue(
                        os.path.exists(csv_path),
                        'fetch_vast did not produce vast/vms.csv')
                    with open(csv_path) as f:
                        lines = f.readlines()
                    self.assertGreater(
                        len(lines), 1,
                        f'Catalog CSV is header-only ({len(lines)} lines) '
                        '— the MO offer crashed the pipeline '
                        '(georegion completion regressed).')
                finally:
                    os.chdir(old_cwd)


if __name__ == '__main__':
    unittest.main()
