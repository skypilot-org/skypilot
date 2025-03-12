# Client-Server Architecture

SkyPilot implements a client-server architecture. When a user runs a command or an API call,
a SkyPilot client issues asynchronous requests to a SkyPilot API server, which
handles all requests.

User-facing docs can be found in [SkyPilot docs](https://docs.skypilot.co/en/latest/docs/index.html).


## High-Level Architecture

![Client-Server Architecture](../../docs/source/images/client-server/high-level-arch.png)

## Detailed Architecture

![Client-Server Architecture](../../docs/source/images/client-server/arch.png)

## Request Executor

See `sky.serve.server.requests` for more details.

![Client-Server Executor](../../docs/source/images/client-server/executor.png)
