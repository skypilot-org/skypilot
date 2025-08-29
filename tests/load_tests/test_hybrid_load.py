"""This test is to test the hybrid load on the server."""
import traceback
import sky
import asyncio
import uuid
import logging
from sky.client import sdk_async as sdk
from sky.utils import common
from sky.jobs.client import sdk_async as jobs_sdk

logger = logging.getLogger(__name__)

def suffix() -> str:
    return str(uuid.uuid4())[:4]

async def down_ignore_error(name: str):
    try:
        await sdk.down(name)
    except Exception as e:
        logger.error(f'Down {name} failed: {e}')

async def large_file_upload(exit: asyncio.Event):
    while not exit.is_set():
        try:
            name = f'largefile-{suffix()}'
            with sky.Dag() as dag:
                sky.Task(name='test', run='echo hello', file_mounts={
                    '/mnt/largefile': './largefile'
                })
            await sdk.launch(task=dag, cluster_name=name)
        except Exception as e:
            logger.error(f'Large file upload ended with error: {e}'
                         f'{traceback.format_exc()}')
            await asyncio.sleep(30)
        finally:
            await down_ignore_error(name)
    logger.info('Large file upload ended')

async def long_tailing(exit: asyncio.Event):
    while not exit.is_set():
        try:
            name = f'longtail-{suffix()}'
            with sky.Dag() as dag:
                sky.Task(name='test', run='for i in {1..300}; do echo "$i" && sleep 1; done')
            await sdk.launch(task=dag, cluster_name=name)
            await sdk.tail_logs(cluster_name=name, job_id=0, follow=True)
        except Exception as e:
            logger.error(f'Long tailing ended with error: {e}'
                         f'{traceback.format_exc()}')
            await asyncio.sleep(30)
        finally:
            await sdk.down(name)
    logger.info('Long tailing ended')

async def jobs_tailing(exit: asyncio.Event):
    while not exit.is_set():
        try:
            name = f'jobs-{suffix()}'
            with sky.Dag() as dag:
                sky.Task(name='test', run='for i in {1..300}; do echo "$i" && sleep 1; done')
            await jobs_sdk.launch(task=dag, name=name)
            await jobs_sdk.tail_logs(name=name, job_id=0, follow=True)
        except Exception as e:
            logger.error(f'Jobs tailing ended with error: {e}'
                         f'{traceback.format_exc()}')
            await asyncio.sleep(30)
    logger.info('Jobs tailing ended')

async def status(exit: asyncio.Event):
    while not exit.is_set():
        try:
            await sdk.status()
        except Exception as e:
            logger.error(f'Status ended with error: {e}'
                         f'{traceback.format_exc()}')
    logger.info('Status ended')

async def status_refresh(exit: asyncio.Event):
    while not exit.is_set():
        try:
            await sdk.status(refresh=common.StatusRefreshMode.FORCE, all_users=True)
        except Exception as e:
            logger.error(f'Status refresh ended with error: {e}'
                         f'{traceback.format_exc()}')
    logger.info('Status refresh ended')

async def hybrid_load(exit: asyncio.Event):
    tasks = []
    for _ in range(2):
        tasks.append(large_file_upload(exit))
    for _ in range(5):
        tasks.append(long_tailing(exit))
    for _ in range(5):
        tasks.append(jobs_tailing(exit))
    for _ in range(10):
        tasks.append(status(exit))
    for _ in range(2):
        tasks.append(status_refresh(exit))
    try:
        await asyncio.gather(*tasks)
    except Exception as e:
        logger.error(f'Hybrid load ended with error: {e}'
                     f'{traceback.format_exc()}')
    finally:
        exit.set()
        logger.info('Hybrid load ended')
