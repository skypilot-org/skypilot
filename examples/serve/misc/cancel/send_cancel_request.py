import asyncio

import aiohttp

redirector_endpoint = input('Enter redirector endpoint: ')


async def fetch(session, url):
    try:
        async with session.get(url) as response:
            print('Got response!')
            return await response.text()
    except asyncio.CancelledError:
        print('Request was cancelled!')
        raise


async def main():
    timeout = 2

    async with aiohttp.ClientSession() as session:
        task = asyncio.create_task(
            fetch(session, f'http://{redirector_endpoint}/'))

        await asyncio.sleep(timeout)
        # We manually cancel requests for test purposes.
        # You could also manually Ctrl + C a curl to cancel a request.
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            print('Main function caught the cancelled exception.')


asyncio.run(main())
