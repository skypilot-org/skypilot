"""Utilities for handling interactive SSH prompts."""
import getpass
import re
import typing

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.client import common as client_common
from sky.server import common as server_common
from sky.utils import rich_utils

if typing.TYPE_CHECKING:
    import aiohttp
else:
    aiohttp = adaptors_common.LazyImport('aiohttp')

logger = sky_logging.init_logger(__name__)

SKY_INPUT_PATTERN = re.compile(r'<sky-input session="([^"]+)"/>')


def _process_prompt_and_get_input(
    line: str
) -> typing.Tuple[typing.Optional[str], typing.Optional[str],
                  typing.Optional[str]]:
    """Parse line for interactive prompt and get user input if found.

    Args:
        line: The log line to check.

    Returns:
        Tuple of (processed_line, session_id, user_input).
        - If no prompt found: (original_line, None, None)
        - If prompt found: (None, session_id, user_input)
    """
    match = SKY_INPUT_PATTERN.search(line)
    if not match:
        return line, None, None

    session_id = match.group(1)

    # Temporarily stop the spinner.
    with rich_utils.client_status('') as status:
        status.stop()
        # Prompt already displayed by backend, just get input.
        try:
            user_input = getpass.getpass('')
        except EOFError:
            user_input = ''

    return None, session_id, user_input


def handle_interactive_prompt(line: str) -> typing.Optional[str]:
    """Handle interactive SSH prompts in streamed logs (sync version).

    Args:
        line: The log line to check for interactive prompt markers.

    Returns:
        The line with the marker removed, or None if this was an interactive
        prompt (meaning the line was consumed and should not be printed).
    """
    processed_line, session_id, user_input = _process_prompt_and_get_input(line)
    if session_id is None:
        return processed_line

    try:
        server_common.make_authenticated_request(
            'POST',
            f'/api/interactive/{session_id}',
            json={'input': user_input},
            timeout=client_common.API_SERVER_REQUEST_CONNECTION_TIMEOUT_SECONDS,
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to send interactive input: {e}')

    return processed_line


async def handle_interactive_prompt_async(line: str) -> typing.Optional[str]:
    """Handle interactive SSH prompts in streamed logs (async version).

    Args:
        line: The log line to check for interactive prompt markers.

    Returns:
        The line with the marker removed, or None if this was an interactive
        prompt (meaning the line was consumed and should not be printed).
    """
    processed_line, session_id, user_input = _process_prompt_and_get_input(line)
    if session_id is None:
        return processed_line

    try:
        async with aiohttp.ClientSession() as session:
            await server_common.make_authenticated_request_async(
                session,
                'POST',
                f'/api/interactive/{session_id}',
                json={'input': user_input},
                timeout=aiohttp.ClientTimeout(
                    connect=client_common.
                    API_SERVER_REQUEST_CONNECTION_TIMEOUT_SECONDS),
            )
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to send interactive input: {e}')

    return processed_line
