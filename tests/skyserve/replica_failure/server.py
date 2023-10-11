import argparse
import functools

from fastapi import FastAPI
import requests
import uvicorn

app = FastAPI()


@functools.lru_cache(maxsize=1)
def get_self_ip() -> str:
    return requests.get('http://ifconfig.me').text


@app.get('/get_ip')
async def get_ip():
    return {'ip': get_self_ip()}


@app.get('/health')
async def health():
    return {'status': 'ok'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SkyServe HTTP Test Server')
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    uvicorn.run(app, host='0.0.0.0', port=args.port)
