import fastapi
from fastapi import security
import uvicorn

app = fastapi.FastAPI()

bearer_security = security.HTTPBearer()
STATIC_TOKEN = "static_secret_token"


def verify_token(
    credentials: security.HTTPAuthorizationCredentials = fastapi.Depends(
        bearer_security),
) -> None:
    if credentials.credentials != STATIC_TOKEN:
        raise fastapi.HTTPException(status_code=403,
                                    detail="Invalid authentication credentials")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/compute")
async def compute(_=fastapi.Depends(verify_token)):
    return {"message": "Token validation successful!"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8087)
