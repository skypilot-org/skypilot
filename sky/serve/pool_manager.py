"""Pool manager for batch jobs submission."""
import asyncio
import dataclasses
import io
import traceback
from typing import Dict, List, Tuple
import uuid

import aiohttp
import fastapi
import uvicorn

import sky
from sky import global_user_state
from sky import sky_logging
from sky.client import sdk
from sky.serve import constants
from sky.usage import usage_lib
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)


class PoolManager:
    """Pool manager for batch jobs submission."""

    def __init__(self, controller_addr: str, load_balancer_port: int):
        self.controller_addr = controller_addr
        self.load_balancer_port = load_balancer_port
        self.app = fastapi.FastAPI()
        self.cn2inproc: Dict[str, int] = {}

    async def _sync_with_controller_once(self) -> None:
        async with aiohttp.ClientSession() as session:
            try:
                # Send request information
                async with session.post(
                        self.controller_addr + '/controller/load_balancer_sync',
                        json={'request_aggregator': {}},
                        timeout=aiohttp.ClientTimeout(5),
                ) as response:
                    # Clean up after reporting request info to avoid OOM.
                    response.raise_for_status()
                    response_json = await response.json()
                    ready_replica_cluster_names = response_json.get(
                        'ready_replica_cluster_names', [])
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error(f'An error occurred when syncing with '
                             f'the controller: {e}'
                             f'\nTraceback: {traceback.format_exc()}')
            else:
                logger.info('Available Replica clusters: '
                            f'{ready_replica_cluster_names}')
                for cn in ready_replica_cluster_names:
                    if cn not in self.cn2inproc:
                        self.cn2inproc[cn] = 0
                cn_to_del = []
                for cn in self.cn2inproc:
                    if cn not in ready_replica_cluster_names:
                        cn_to_del.append(cn)
                for cn in cn_to_del:
                    del self.cn2inproc[cn]

    async def _sync_with_controller(self):
        """Sync with controller periodically.

        Every `constants.LB_CONTROLLER_SYNC_INTERVAL_SECONDS` seconds, the
        load balancer will sync with the controller to get the latest
        information about available replicas; also, it report the request
        information to the controller, so that the controller can make
        autoscaling decisions.
        """
        # Sleep for a while to wait the controller bootstrap.
        await asyncio.sleep(5)

        while True:
            try:
                await self._sync_with_controller_once()
                await asyncio.sleep(
                    constants.LB_CONTROLLER_SYNC_INTERVAL_SECONDS)
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'An error occurred when syncing with '
                             f'the controller: {e}'
                             f'\nTraceback: {traceback.format_exc()}')

    def _try_cancel_all_jobs(self, cluster_name: str):
        from sky import core  # pylint: disable=import-outside-toplevel

        handle = global_user_state.get_handle_from_cluster_name(cluster_name)
        if handle is None:
            return
        try:
            usage_lib.messages.usage.set_internal()
            # Note that `sky.cancel()` may not go through for a variety of
            # reasons:
            # (1) head node is preempted; or
            # (2) somehow user programs escape the cancel codepath's kill.
            # The latter is silent and is a TODO.
            #
            # For the former, an exception will be thrown, in which case we
            # fallback to terminate_cluster() in the except block below. This
            # is because in the event of recovery on the same set of remaining
            # worker nodes, we don't want to leave some old job processes
            # running.
            # TODO(zhwu): This is non-ideal and we should figure out another way
            # to reliably cancel those processes and not have to down the
            # remaining nodes first.
            #
            # In the case where the worker node is preempted, the `sky.cancel()`
            # should be functional with the `_try_cancel_if_cluster_is_init`
            # flag, i.e. it sends the cancel signal to the head node, which will
            # then kill the user process on remaining worker nodes.
            core.cancel(cluster_name=cluster_name,
                        all=True,
                        _try_cancel_if_cluster_is_init=True)
        except Exception as e:  # pylint: disable=broad-except
            logger.info('Failed to cancel the job on the cluster. The cluster '
                        'might be already down or the head node is preempted.'
                        '\n  Detailed exception: '
                        f'{common_utils.format_exception(e)}\n'
                        'Terminating the cluster explicitly to ensure no '
                        'remaining job process interferes with recovery.')

    def run(self):

        @self.app.post('/acquire')
        async def acquire(request: fastapi.Request):
            cn = None
            for cn in self.cn2inproc:
                if self.cn2inproc[cn] == 0:
                    self.cn2inproc[cn] = 1
                    break
            return fastapi.responses.JSONResponse({
                'cluster_name': cn,
            })

        @self.app.post('/release')
        async def release(request: fastapi.Request):
            payload = await request.json()
            cn = payload['cluster_name']
            if cn not in self.cn2inproc:
                return fastapi.responses.JSONResponse({
                    'successful': False,
                    'message': f'Cluster {cn} not found.',
                })
            if self.cn2inproc[cn] == 0:
                return fastapi.responses.JSONResponse({
                    'successful': False,
                    'message': f'Cluster {cn} is not in use.',
                })
            self.cn2inproc[cn] = 0
            self._try_cancel_all_jobs(cn)
            return fastapi.responses.JSONResponse({
                'successful': True,
                'message': f'Cluster {cn} released.',
            })

        @self.app.on_event('startup')
        async def startup():
            asyncio.create_task(self._sync_with_controller())
            asyncio.create_task(self._process_request())

        uvicorn.run(self.app, host='0.0.0.0', port=self.load_balancer_port)


def run_pool_manager(controller_addr: str, load_balancer_port: int,
                     *unused_args):
    del unused_args  # Unused.
    pool_manager = PoolManager(controller_addr, load_balancer_port)
    pool_manager.run()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--controller-addr',
                        required=True,
                        default='127.0.0.1',
                        help='The address of the controller.')
    parser.add_argument('--load-balancer-port',
                        type=int,
                        required=True,
                        default=8890,
                        help='The port where the load balancer listens to.')
    args = parser.parse_args()
    run_pool_manager(args.controller_addr, args.load_balancer_port)
