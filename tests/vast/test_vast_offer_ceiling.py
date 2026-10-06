"""The claim's proven price ceiling must bind the launch's offer choice.

The controller's quote proves a CATALOG BUCKET price against the budget
cap, but buckets refresh on a cadence while offers reprice live -- so a
bucket quoted at $1.60 can hold (or reprice to) a $3.00 offer, and the
launch used to rent the first surviving offer regardless (CodeRabbit,
skypilot-controller#512 finding 3). The fix: the runner threads the
claim's proven whole-instance ceiling into create_instance_kwargs (the
control-key channel 'login'/'api_key' already use), and launch() refuses
every offer whose live whole-box dph_total exceeds it, fail-closed.

Contracts pinned here:

  * every survivor over the ceiling -> the TYPED refusal (a human
    decision, distinguishable from the stockout RuntimeError by class
    AND message), carrying the provider body: machine ids, live prices,
    the ceiling, the query;
  * an offer that cannot be priced (no dph_total) cannot be proven under
    the cap and is refused too;
  * among survivors the CHEAPEST offer is rented (price ascending,
    machine id as the deterministic tie-break), never the API's
    relevance-score order;
  * the control key never reaches the Vast API create call;
  * no ceiling (config-file-only launches) keeps the unfiltered behavior
    exactly, apart from the cheapest-first order.
"""
import unittest
from unittest import mock

from sky.provision.vast import utils


def _offer(offer_id, machine_id, price, gpu_name='RTX PRO 6000 WS'):
    offer = {
        'id': offer_id,
        'machine_id': machine_id,
        'gpu_name': gpu_name,
        'num_gpus': 1,
        'cpu_cores': 32,
        'cpu_ram': 65536,
        'geolocation': 'Czechia, CZ, EU',
        'hosting_type': 1,
    }
    if price is not None:
        offer['dph_total'] = price
    return offer


def _launch(client, create_instance_kwargs=None):
    with mock.patch.object(utils, 'vast') as vast_mod:
        vast_mod.vast.return_value = client
        return utils.launch(
            name='test-cluster',
            instance_type='1x-RTX_PRO_6000_WS-32-65536',
            region='EU',
            disk_size=256,
            image_name='docker.io/vastai/kvm',
            ports=[22],
            preemptible=False,
            secure_only=True,
            create_instance_kwargs=create_instance_kwargs,
        )


def _client(offers):
    client = mock.Mock()
    client.search_offers.return_value = offers
    client.create_instance.return_value = {'new_contract': 'inst-ok'}
    client.show_instance.return_value = {'id': 'vast-53903136'}
    return client


class TestClaimCeilingFilter(unittest.TestCase):

    def test_every_survivor_over_the_ceiling_refuses_with_the_typed_error(self):
        client = _client([
            _offer(111, 152942, 3.00),
            _offer(222, 152943, 2.75),
        ])

        with self.assertRaises(utils.VastOfferPriceExceedsCeilingError) as ctx:
            _launch(client, {'max_hourly_cost_usd': 2.40})

        message = str(ctx.exception)
        # The provider body is preserved: machine ids, live prices, the
        # ceiling, the query -- so the runner can tell this from a
        # stockout by class alone and an operator can act on the message.
        self.assertIn('price ceiling', message)
        self.assertIn('$2.4', message)
        self.assertIn('152942', message)
        self.assertIn('$3.0', message)
        self.assertIn('152943', message)
        client.create_instance.assert_not_called()

    def test_a_stockout_is_the_plain_runtime_error_not_the_typed_refusal(self):
        client = _client([])

        with self.assertRaises(RuntimeError) as ctx:
            _launch(client, {'max_hourly_cost_usd': 2.40})

        self.assertNotIsInstance(ctx.exception,
                                 utils.VastOfferPriceExceedsCeilingError)
        self.assertIn('could not find an offer', str(ctx.exception))

    def test_the_under_ceiling_offer_launches_and_the_over_one_does_not(self):
        client = _client([
            _offer(111, 152942, 3.00),
            _offer(222, 152943, 1.60),
        ])

        result = _launch(client, {'max_hourly_cost_usd': 2.40})

        self.assertEqual(result, 'vast-53903136')
        self.assertEqual(client.create_instance.call_args.kwargs['id'], 222)

    def test_the_cheapest_survivor_wins_regardless_of_api_order(self):
        client = _client([
            _offer(111, 152942, 1.74),
            _offer(222, 152943, 1.47),
        ])

        _launch(client, {'max_hourly_cost_usd': 2.40})

        self.assertEqual(client.create_instance.call_args.kwargs['id'], 222)

    def test_an_unpriced_offer_cannot_be_proven_under_the_cap(self):
        client = _client([_offer(111, 152942, None)])

        with self.assertRaises(utils.VastOfferPriceExceedsCeilingError):
            _launch(client, {'max_hourly_cost_usd': 2.40})

        client.create_instance.assert_not_called()

    def test_the_ceiling_control_key_never_reaches_the_api(self):
        client = _client([_offer(111, 152942, 1.60)])

        _launch(client, {'max_hourly_cost_usd': 2.40, 'label': 'x'})

        kwargs = client.create_instance.call_args.kwargs
        self.assertNotIn(utils.MAX_HOURLY_COST_USD_KWARG, kwargs)
        # The caller's real API kwargs still pass through.
        self.assertEqual(kwargs['label'], 'x')

    def test_no_ceiling_keeps_the_unfiltered_behavior(self):
        client = _client([
            _offer(111, 152942, 3.00),
            _offer(222, 152943, 1.60),
        ])

        _launch(client)

        # No ceiling: both offers are candidates (cheapest first).
        self.assertEqual(client.create_instance.call_args.kwargs['id'], 222)


class TestCeilingConfigChannel(unittest.TestCase):
    """The runner -> Resources -> template -> provider_config channel."""

    def test_cluster_config_overrides_reach_create_instance_kwargs(self):
        """A Resources carrying _cluster_config_overrides must flow the
        ceiling into make_deploy_resources_variables' create_instance_kwargs
        -- the exact path provision.instance reads provider_config from."""
        from sky import clouds
        from sky import resources as resources_lib
        from sky.clouds import vast as vast_cloud

        resources = resources_lib.Resources(
            cloud=vast_cloud.Vast(),
            instance_type='1x-RTX_PRO_6000_WS-32-65536',
            region='EU',
            _cluster_config_overrides={
                'vast': {
                    'create_instance_kwargs': {
                        'max_hourly_cost_usd': 2.40,
                    },
                },
            },
        )
        region = clouds.Region('EU')

        with mock.patch.object(
                vast_cloud.Vast,
                'get_accelerators_from_instance_type',
                return_value={'RTXPRO6000': 1},
        ):
            variables = vast_cloud.Vast().make_deploy_resources_variables(
                resources=resources,
                cluster_name=mock.Mock(),
                region=region,
                zones=None,
                num_nodes=1,
                dryrun=True,
            )

        self.assertEqual(
            variables['create_instance_kwargs'],
            {'max_hourly_cost_usd': 2.40},
            variables,
        )


if __name__ == '__main__':
    unittest.main()
