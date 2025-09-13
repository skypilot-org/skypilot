#!/usr/bin/env python3
"""Example RESTful admin policy server for SkyPilot."""

import argparse

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

    try:
        # Apply validation and mutation using the loaded policy
        mutated_request = request.app.state.policy_impl.apply(user_request)
    except Exception as e:  # pylint: disable=broad-except
        return JSONResponse(content=str(e), status_code=400)
    return JSONResponse(content=mutated_request.encode())


class SetAutostoPolicy(sky.AdminPolicy):
    """Example: implement a policy at server."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        task = user_request.task
        for r in task.resources:
            r.override_autostop_config(idle_minutes=10)
        return sky.MutatedUserRequest(
            task=task, skypilot_config=user_request.skypilot_config)


@app.post('/set_autostop')
async def set_autostop(request: Request) -> JSONResponse:
    """Example: apply the above policy at the API handler."""
    json_data = await request.json()
    user_request = sky.UserRequest.decode(json_data)
    mutated_request = SetAutostoPolicy.validate_and_mutate(user_request)
    return JSONResponse(content=mutated_request.encode())


@app.get('/')
async def health_check():
    return 'OK'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host',
                        default='0.0.0.0',
                        help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--port',
                        type=int,
                        default=8080,
                        help='Port to bind to (default: 8080)')
    parser.add_argument('--policy',
                        default='DoNothingPolicy',
                        help='Policy to use (default: DoNothingPolicy)')
    args = parser.parse_args()
    policy_class = getattr(example_policy, args.policy)
    assert issubclass(
        policy_class,
        sky.AdminPolicy), f'Policy {args.policy} is not a valid admin policy'
    app.state.policy_impl = policy_class()
    uvicorn.run(app,
                workers=1,
                host=args.host,
                port=args.port,
                log_level="info")
