"""Pool manager for batch jobs submission."""
import asyncio
import traceback
from typing import Dict

import aiohttp
import fastapi
import uvicorn

from sky import sky_logging
from sky.serve import constants

logger = sky_logging.init_logger(__name__)


class PoolManager:
    """Pool manager for batch jobs submission."""

    def __init__(self, controller_addr: str, load_balancer_port: int):
        self.controller_addr = controller_addr
        self.load_balancer_port = load_balancer_port
        self.app = fastapi.FastAPI()
        self.cn2inproc: Dict[str, int] = {}
        self.lock = asyncio.Lock()

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
                for cn in ready_replica_cluster_names:
                    if cn not in self.cn2inproc:
                        self.cn2inproc[cn] = 0
                cn_to_del = []
                for cn in self.cn2inproc:
                    if cn not in ready_replica_cluster_names:
                        cn_to_del.append(cn)
                for cn in cn_to_del:
                    del self.cn2inproc[cn]
                logger.info(f'Available Replica clusters: {self.cn2inproc}')

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

    def run(self):

        @self.app.post('/acquire')
        async def acquire(request: fastapi.Request):
            async with self.lock:
                selected_cn = None
                for cn in self.cn2inproc:
                    if self.cn2inproc[cn] == 0:
                        self.cn2inproc[cn] = 1
                        selected_cn = cn
                        break
                logger.info(f'Acquire cluster. Assigned cluster: {selected_cn}')
                return fastapi.responses.JSONResponse({
                    'cluster_name': selected_cn,
                })

        @self.app.post('/release')
        async def release(request: fastapi.Request):
            async with self.lock:
                payload = await request.json()
                cn = payload['cluster_name']
                logger.info(f'Release cluster: {cn}')
                if cn not in self.cn2inproc:
                    logger.info(f'Cluster {cn} not found.')
                    return fastapi.responses.JSONResponse({
                        'successful': False,
                        'message': f'Cluster {cn} not found.',
                    })
                if self.cn2inproc[cn] == 0:
                    logger.info(f'Cluster {cn} is not in use.')
                    return fastapi.responses.JSONResponse({
                        'successful': False,
                        'message': f'Cluster {cn} is not in use.',
                    })
                self.cn2inproc[cn] = 0
                logger.info(f'Cluster {cn} released.')
                return fastapi.responses.JSONResponse({
                    'successful': True,
                    'message': f'Cluster {cn} released.',
                })

        @self.app.get('/debug')
        async def debug(request: fastapi.Request):
            async with self.lock:
                return fastapi.responses.JSONResponse({
                    'cn2inproc': self.cn2inproc,
                })

        @self.app.on_event('startup')
        async def startup():
            asyncio.create_task(self._sync_with_controller())

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
