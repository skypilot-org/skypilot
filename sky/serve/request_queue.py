"""Request queue for batch jobs submission."""
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
from sky import sky_logging
from sky.client import sdk
from sky.serve import constants

logger = sky_logging.init_logger(__name__)


@dataclasses.dataclass
class RequestEntry:
    batch_id: str
    run_script: str


@dataclasses.dataclass
class RequestStatus:
    batch_size: int
    num_pending_reqs: int = 0
    submitting_reqs: List[str] = dataclasses.field(default_factory=list)
    # List[job_id, cn]
    submitted_reqs: List[Tuple[int,
                               str]] = dataclasses.field(default_factory=list)
    # List[logs]
    completed_reqs: List[str] = dataclasses.field(default_factory=list)


class RequestQueue:
    """Request queue for batch jobs submission."""

    def __init__(self, controller_addr: str, load_balancer_port: int):
        self.controller_addr = controller_addr
        self.load_balancer_port = load_balancer_port
        self.app = fastapi.FastAPI()
        self.request_queue: asyncio.Queue[RequestEntry] = asyncio.Queue()
        self.cn2inproc: Dict[str, int] = {}
        self.id2status: Dict[str, RequestStatus] = {}

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

    async def _process_request(self):
        while True:
            await self._pull_request_status()
            await self._process_request_entry()
            await asyncio.sleep(0.01)

    async def _process_request_entry(self) -> None:
        avail = None
        for cn, inproc in self.cn2inproc.items():
            if inproc == 0:
                avail = cn
                break
        if avail is None:
            return
        request_entry = await self.request_queue.get()
        self.cn2inproc[avail] += 1
        logger.info(
            f'Processing request from batch {request_entry.batch_id} on {avail}'
        )
        sky_req_id = sdk.exec(task=sky.Task(run=request_entry.run_script),
                              cluster_name=avail)
        self.id2status[request_entry.batch_id].submitting_reqs.append(
            sky_req_id)
        self.id2status[request_entry.batch_id].num_pending_reqs -= 1

    def _is_finished(self, job_id: int, cn: str) -> bool:
        job_statuses = sdk.get(sdk.job_status(cn, [job_id]))
        return job_statuses[job_id] == 'SUCCEEDED'

    def check_request_status(self, batch_id: str,
                             status: RequestStatus) -> None:
        logger.info(f'Checking status for {batch_id}')
        # TODO(tian): Make this async.
        api_stats = sdk.api_status(request_ids=status.submitting_reqs,
                                   all_status=True)
        for stat in api_stats:
            if stat.status == 'SUCCEEDED':
                job_id, handle = sdk.get(stat.request_id)
                cn = handle.cluster_name
                status.submitted_reqs.append((job_id, cn))
                status.submitting_reqs.remove(stat.request_id)
                self.cn2inproc[cn] -= 1

    def check_request_finished(self, batch_id: str,
                               status: RequestStatus) -> None:
        logger.info(f'Checking request finished for {batch_id}')
        to_remove = []
        for job_id, cn in status.submitted_reqs:
            if not self._is_finished(job_id, cn):
                continue
            stream = io.StringIO()
            sdk.tail_logs(cluster_name=cn,
                          job_id=job_id,
                          follow=False,
                          output_stream=stream)
            logs = stream.getvalue()
            status.completed_reqs.append(logs)
            to_remove.append((job_id, cn))
        for job_id, cn in to_remove:
            status.submitted_reqs.remove((job_id, cn))
            self.cn2inproc[cn] -= 1

    async def _pull_request_status(self) -> None:
        for batch_id, status in self.id2status.items():
            if status.submitting_reqs:
                self.check_request_status(batch_id, status)
            if status.submitted_reqs:
                self.check_request_finished(batch_id, status)

    def run(self):

        @self.app.post('/submit')
        async def submit(request: fastapi.Request):
            payload = await request.json()
            num_reqs = payload['num_reqs']
            batch_id = f'sky-batch-{str(uuid.uuid4())[:4]}'
            status = RequestStatus(batch_size=num_reqs,
                                   num_pending_reqs=num_reqs)
            self.id2status[batch_id] = status
            for _ in range(num_reqs):
                await self.request_queue.put(
                    RequestEntry(batch_id, payload['run_script']))
            return fastapi.responses.JSONResponse({
                'message': (f'{num_reqs} requests submitted. '
                            f'Batch ID: {batch_id}')
            })

        @self.app.get('/debug')
        async def debug(request: fastapi.Request):
            return fastapi.responses.JSONResponse({
                'vars': {
                    'id2status': {
                        rdi: dataclasses.asdict(status)
                        for rdi, status in self.id2status.items()
                    },
                    'cn2inproc': self.cn2inproc,
                    'request_queue': [
                        dataclasses.asdict(req)
                        for req in self.request_queue._queue  # type: ignore  # pylint: disable=protected-access
                    ],
                },
            })

        @self.app.get('/query')
        async def query(request: fastapi.Request, batch_id: str):
            if batch_id not in self.id2status:
                return fastapi.responses.JSONResponse({
                    'message': f'Batch {batch_id} not found.',
                })
            status = self.id2status[batch_id]
            msg = f'{status.num_pending_reqs} requests are pending.\n'
            msg += f'{len(status.submitting_reqs)} requests are submitting.\n'
            msg += f'{len(status.submitted_reqs)} requests are submitted.\n'
            msg += f'{len(status.completed_reqs)} requests are completed.\n'
            for i, logs in enumerate(status.completed_reqs):
                rid_identity = (f' Logs for {batch_id} '
                                f'({i+1}/{status.batch_size}) ')
                msg += f'{rid_identity:=^70}\n{logs}\n'
            return fastapi.responses.JSONResponse({
                'message': msg,
            })

        @self.app.on_event('startup')
        async def startup():
            asyncio.create_task(self._sync_with_controller())
            asyncio.create_task(self._process_request())

        uvicorn.run(self.app, host='0.0.0.0', port=self.load_balancer_port)


def run_request_queue(controller_addr: str, load_balancer_port: int,
                      *unused_args):
    del unused_args  # Unused.
    request_queue = RequestQueue(controller_addr, load_balancer_port)
    request_queue.run()


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
    run_request_queue(args.controller_addr, args.load_balancer_port)
