import fastapi
import uvicorn

app = fastapi.FastAPI()


@app.get("/")
async def main():
    return {"message": "Hello World"}


if __name__ == "__main__":
    uvicorn.run(app,
                host="0.0.0.0",
                port=8080,
                log_level="info",
                ssl_certfile="cert.pem",
                ssl_keyfile="key.pem",
                ssl_keyfile_password="passphrase")
