import argparse
import asyncio
import subprocess

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import uvicorn

app = FastAPI()
sentence_length = 5
sentence = 'The quick brown fox jumps over the lazy dog'
words = sentence.split()
_cached_ip = None


@app.get('/health')
async def health_check():
    return {'status': 'healthy'}


@app.get('/ip')
async def get_ip():
    return {'ip': _cached_ip}


@app.get('/stream')
async def stream_response():

    async def generate():
        base_length = len(words)
        for i in range(sentence_length):
            word = words[i % base_length]
            yield f'{word}\n'
            await asyncio.sleep(0.2)

    return StreamingResponse(generate(), media_type='text/plain')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='FastAPI Server')
    parser.add_argument('--port',
                        type=int,
                        default=8080,
                        help='Port to run the server on')
    parser.add_argument('--length',
                        type=int,
                        default=5,
                        help='Length of sentence to stream')
    args = parser.parse_args()

    sentence_length = args.length
    _cached_ip = subprocess.check_output(
        'curl -s https://checkip.amazonaws.com',
        shell=True).decode('utf-8').strip()
    uvicorn.run(app, host='0.0.0.0', port=args.port)
