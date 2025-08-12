#!/usr/bin/env python3
"""No-op RESTful admin policy server for testing purpose."""

import argparse

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse
import uvicorn

import sky

app = FastAPI(title="No-op Admin Policy Server", version="1.0.0")


class DoNothingPolicy(sky.AdminPolicy):
    """Example policy: do nothing."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Returns the user request unchanged."""
        return sky.MutatedUserRequest(user_request.task,
                                      user_request.skypilot_config)


@app.post('/')
async def apply_policy(request: Request) -> JSONResponse:
    """Apply an admin policy loaded from external package to a user request"""
    # Decode
    json_data = await request.json()
    user_request = sky.UserRequest.decode(json_data)
    try:
        mutated_request = DoNothingPolicy.validate_and_mutate(user_request)
        user_request.task = mutated_request.task
        user_request.skypilot_config = mutated_request.skypilot_config
    except Exception as e:  # pylint: disable=broad-except
        return JSONResponse(content=str(e), status_code=400)
    return JSONResponse(content=mutated_request.encode())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host',
                        default='0.0.0.0',
                        help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--port',
                        type=int,
                        default=8080,
                        help='Port to bind to (default: 8080)')
    args = parser.parse_args()
    uvicorn.run(app,
                workers=1,
                host=args.host,
                port=args.port,
                log_level="info")
