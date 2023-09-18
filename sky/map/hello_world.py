"""Hello World application for FastAPI.
"""
import fastapi
import uvicorn

app = fastapi.FastAPI()


@app.get('/')
def hello_world():
    user_data = {'data': 'Hello, World!'}
    return fastapi.responses.JSONResponse(content=user_data, status_code=200)


@app.get('/health')
def health():
    user_data = {'data': 'Healthy!'}
    return fastapi.responses.JSONResponse(content=user_data, status_code=200)


if __name__ == '__main__':

    print('serving at port', 8081)
    uvicorn.run(app, host='0.0.0.0', port=8081)
