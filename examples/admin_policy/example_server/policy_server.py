#!/usr/bin/env python3
"""Example RESTful admin policy server for SkyPilot."""

import argparse
from typing import List

import example_policy
from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse
import uvicorn

import sky

app = FastAPI(title="Example Admin Policy Server", version="1.0.0")


@app.post('/')
async def apply_policy(request: Request) -> JSONResponse:
    """Apply an admin policy loaded from external package to a user request"""
    # Decode from request body
    json_data = await request.json()
    user_request = sky.UserRequest.decode(json_data)
    # Change the following list to apply one or more policies to the user
    # request. You can also implement custom policies in server in addition to
    # loading policies from external packages.
    policies: List[sky.AdminPolicy] = [
        example_policy.SetMaxAutostopIdleMinutesPolicy,
        example_policy.UseSpotForGpuPolicy,
    ]
    try:
        for policy in policies:
            mutated_request = policy.validate_and_mutate(user_request)
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
