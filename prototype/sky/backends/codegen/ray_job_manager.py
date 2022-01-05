import asyncio
from ray.dashboard.modules.job import sdk

client = sdk.JobSubmissionClient('127.0.0.1:8265')


async def _tail_logs(client: JobSubmissionClient, job_id: str):
    async for lines in client.tail_job_logs(job_id):
        print(lines, end="")
