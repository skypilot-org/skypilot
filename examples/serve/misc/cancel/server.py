import argparse
import asyncio

from aiohttp import web


async def handle(request):
    response = web.StreamResponse()
    await response.prepare(request)

    try:
        # Simulate a computation that takes 10 seconds
        for i in range(10):
            print('Computing... step', i)
            await asyncio.sleep(1)
            await response.write(b' ')  # Sending a space as a heartbeat
        await response.write(b'Completed after 10 seconds.')
    except (asyncio.CancelledError, ConnectionResetError):
        print('Client disconnected, stopping computation.')
        return response

    return response


async def health_check(request):
    print('Received health check')
    return web.Response(text='Healthy')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SkyServe HTTP Test Server')
    parser.add_argument('--port', type=int, required=False, default=8081)
    args = parser.parse_args()
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_get('/', handle)
    web.run_app(app, host='0.0.0.0', port=args.port)
