"""Manager of requests."""

import time
from typing import Any, Optional

from sky import sky_logging
from sky.server.requests import event_bus
from sky.server.requests import requests as requests_lib

logger = sky_logging.init_logger(__name__)


async def set_request_succeeded_async(request_id: str,
                                      result: Optional[Any]) -> None:
    """Async version of set_request_succeeded."""
    async with requests_lib.update_request_async(request_id) as request_task:
        assert request_task is not None, request_id
        request_task.status = requests_lib.RequestStatus.SUCCEEDED
        request_task.finished_at = time.time()
        if result is not None:
            request_task.set_return_value(result)
    logger.info(f'Request {request_id} succeeded, publish event to event bus')
    await event_bus.publish(request_id, requests_lib.RequestStatus.SUCCEEDED)
