import argparse
import asyncio

import fastapi
import uvicorn

app = fastapi.FastAPI()


@app.get('/')
async def root():
    return 'Hi, SkyPilot here!'


@app.get('/health')
async def health():
    # Simulate a readiness probe with long processing time.
    await asyncio.sleep(20)
    return {'status': 'ok'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='SkyServe Readiness Timeout Test Server')
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    uvicorn.run(app, host='0.0.0.0', port=args.port)
