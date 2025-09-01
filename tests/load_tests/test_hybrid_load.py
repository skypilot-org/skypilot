"""This test is to test the hybrid load on the server."""
import asyncio
import os
import time
import traceback
import uuid
import signal

import sky
from sky import sky_logging
from sky.client import sdk_async as sdk
from sky.jobs.client import sdk_async as jobs_sdk
from sky.utils import common
from sky.utils import context
from sky.utils import context_utils

logger = sky_logging.init_logger(__name__)


def suffix() -> str:
    return str(uuid.uuid4())[:4]

class Workload():

    def __init__(self, name, exit_event, load_fn, cleanup_fn):
        self.name = name
        self.exit_event = exit_event
        self.load_fn = load_fn
        self.cleanup_fn = cleanup_fn

    async def run(self):
        context.initialize()
        ctx = context.get()
        assert ctx is not None
        uid = suffix()
        log_file = f'hybrid_load_logs/{self.name}-{uid}.log'
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        origin = ctx.redirect_log(f'hybrid_load_logs/{self.name}-{uid}.log')
        count = 0
        failed = 0
        total_duration = 0
        now = time.time()
        while not self.exit_event.is_set():
            try:
                await self.load_fn()
                count += 1
                total_duration += time.time() - now
                now = time.time()
            except Exception as e:
                failed += 1
                logger.error(f'Workload {self.name} failed: {e}')
            finally:
                await self.cleanup_ignore_error()
        ctx.redirect_log(origin)
        logger.info('Workload completed',
                    extra={
                        'name': self.name,
                        'count': count,
                        'failed': failed,
                        'total_duration': total_duration,
                        'average_duration': total_duration /
                                            count if count > 0 else 0
                    })

    async def cleanup_ignore_error(self):
        if self.cleanup_fn is None:
            return
        try:
            await self.cleanup_fn()
        except Exception as e:
            logger.error(f'Workload {self.name} cleanup failed: {e}')


async def large_file_upload(exit: asyncio.Event):
    uid = suffix()

    async def launch():
        name = f'largefile-{uid}'
        with sky.Dag() as dag:
            sky.Task(name='test',
                     run='echo hello',
                     file_mounts={'/mnt/largefile': './largefile'})
        await sdk.launch(task=dag, cluster_name=name)

    async def down():
        await sdk.down(f'largefile-{uid}')

    return await Workload('large_file_upload', exit, launch, down).run()


async def long_tailing(exit: asyncio.Event):
    uid = suffix()

    async def launch():
        name = f'longtail-{uid}'
        with sky.Dag() as dag:
            sky.Task(name='test',
                     run='for i in {1..300}; do echo "$i" && sleep 1; done')
        await sdk.launch(task=dag, cluster_name=name)

    async def down():
        await sdk.down(f'longtail-{uid}')

    return await Workload('long_tailing', exit, launch, down).run()


async def jobs_tailing(exit: asyncio.Event):
    uid = suffix()

    async def launch():
        name = f'jobs-{uid}'
        with sky.Dag() as dag:
            sky.Task(name='test',
                     run='for i in {1..300}; do echo "$i" && sleep 1; done')
        await jobs_sdk.launch(task=dag, name=name)

    async def cancel():
        await jobs_sdk.cancel(f'jobs-{uid}')

    return await Workload('jobs_tailing', exit, launch, cancel).run()


async def status(exit: asyncio.Event):
    return await Workload('status', exit, sdk.status, None).run()


async def status_refresh(exit: asyncio.Event):

    async def fn():
        await sdk.status(refresh=common.StatusRefreshMode.FORCE, all_users=True)

    return await Workload('status_refresh', exit, fn, None).run()

async def api_status(exit: asyncio.Event):
    return await Workload('api_status', exit, sdk.api_status, None).run()


async def hybrid_load(exit: asyncio.Event):
    tasks = []
    for _ in range(1):
        tasks.append(large_file_upload(exit))
    for _ in range(2):
        tasks.append(long_tailing(exit))
    for _ in range(2):
        tasks.append(status(exit))
    for _ in range(1):
        tasks.append(status_refresh(exit))
    for _ in range(1):
        tasks.append(api_status(exit))
    try:
        await asyncio.gather(*tasks)
    except Exception as e:
        logger.error(f'Hybrid load ended with error: {e}'
                     f'{traceback.format_exc()}')
    finally:
        exit.set()
        logger.info('Hybrid load ended')


async def main():
    context_utils.hijack_sys_attrs()
    exit_event = asyncio.Event()
    main_task = asyncio.create_task(hybrid_load(exit_event))
    
    def signal_handler():
        logger.info('Received interrupt signal, initiating graceful shutdown...')
        exit_event.set()
    
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, signal_handler)

    try:
        await main_task
    except asyncio.CancelledError:
        logger.info('Main task was cancelled')
    finally:
        logger.info('Application shutdown complete')


if __name__ == '__main__':
    asyncio.run(main())
