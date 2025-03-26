import asyncio
import collections
import random
import statistics
import time

import aiohttp

num_req = 40

host = '0.0.0.0'
# host = 'test3.aws.cblmemo.net'

# port = 6999
port = 6001
# port = 8000

res = collections.defaultdict(int)


async def send_request(session, sleep_time):
    start_time = time.time()
    st = random.choice(list(range(1, 10)))
    # async with session.post(f'http://{host}:{port}/sleep',
    #                        json={'time_to_sleep': st}) as response:
    time_to_wait = random.random()
    await asyncio.sleep(time_to_wait)
    async with session.get(
            f'http://{host}:{port}/',
            headers={
                # async with session.get(f'http://{host}:{port}/stream', headers={
                'x-hash-key': 'true'
            }) as response:
        # async with session.get(f'http://{host}:{port}/non-stream') as response:
        try:
            js = await response.json()
            print('json', js)
        except aiohttp.client_exceptions.ContentTypeError:
            # Handle text/plain content type
            text = await response.text()
            result = text.split('\n')[-2]
            print(result)
            res[result] += 1
    end_time = time.time()
    # print('===req finished===')
    return end_time - start_time


async def main():
    # List to store all latencies
    latencies = []

    # Create a client session
    async with aiohttp.ClientSession() as session:
        # Create tasks for each sleep time
        tasks_1s = [send_request(session, 1) for _ in range(num_req)]
        # tasks_10s = [send_request(session, 10) for _ in range(num_req)]

        # Create a semaphore to limit concurrency to 20
        semaphore = asyncio.Semaphore(100)

        async def bounded_send_request(task_func):
            async with semaphore:
                return await task_func

        # Wrap all tasks with the semaphore
        bounded_tasks_1s = [bounded_send_request(task) for task in tasks_1s]
        # bounded_tasks_10s = [bounded_send_request(task) for task in tasks_10s]

        # Gather all results
        results_1s = await asyncio.gather(*bounded_tasks_1s)
        # results_10s = await asyncio.gather(*bounded_tasks_10s)

        # Combine results
        latencies = results_1s  # + results_10s

    # Calculate and print statistics
    avg_latency = statistics.mean(latencies)
    print(f'Average latency: {avg_latency:.4f} seconds')
    print(f'Min latency: {min(latencies):.4f} seconds')
    print(f'Max latency: {max(latencies):.4f} seconds')
    print(f'Median latency: {statistics.median(latencies):.4f} seconds')
    print(f'res: {res}')


# Run the async main function
if __name__ == '__main__':
    asyncio.run(main())
