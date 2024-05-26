import asyncio

import fastapi
import uvicorn

with open('example.txt', 'r') as f:
    WORD_TO_STREAM = f.read()

app = fastapi.FastAPI()


@app.get('/')
async def stream():

    async def generate_words():
        for word in WORD_TO_STREAM.split():
            yield word + "\n"
            await asyncio.sleep(0.2)

    return fastapi.responses.StreamingResponse(generate_words(),
                                               media_type="text/plain")


uvicorn.run(app, host='0.0.0.0', port=8080)
