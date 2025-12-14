"""Utilities for handling interactive SSH prompts."""
import base64
import getpass
import logging
import re
import typing

from rich.console import Console

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.server import common as server_common

if typing.TYPE_CHECKING:
    import aiohttp
    import requests
else:
    aiohttp = adaptors_common.LazyImport('aiohttp')
    requests = adaptors_common.LazyImport('requests')

logger = sky_logging.init_logger(__name__)

SKY_INPUT_PATTERN = re.compile(r'<sky-input session="([^"]+)" prompt="([^"]+)"/>')


def _process_prompt_and_get_input(
        line: str,
        output_stream: typing.Optional[typing.Any]) -> typing.Tuple[
            typing.Optional[str], typing.Optional[str], typing.Optional[str]]:
    """Parse line for interactive prompt and get user input if found.
    
    Args:
        line: The log line to check.
        output_stream: The output stream to display prompts to.
    
    Returns:
        Tuple of (processed_line, session_id, user_input).
        - If no prompt found: (original_line, None, None)
        - If prompt found: (cleaned_line, session_id, user_input)
    """
    match = SKY_INPUT_PATTERN.search(line)
    
    if not match:
        return line, None, None
    
    session_id = match.group(1)
    prompt_b64 = match.group(2)
    
    # Decode the prompt text
    try:
        prompt_text = base64.b64decode(prompt_b64).decode('utf-8',
                                                          errors='replace')
    except Exception:
        prompt_text = '[Input required]'
    
    # Remove control sequence from displayed output
    line = SKY_INPUT_PATTERN.sub('', line)
    
    # Display any remaining output first
    if line.strip():
        print(line, flush=True, end='', file=output_stream)
    
    # Display prompt using rich
    try:
        console = Console(file=output_stream)
        console.print(prompt_text, end='', highlight=False)
    except Exception:
        print(prompt_text, flush=True, end='', file=output_stream)
    
    # Prompt user for input (using getpass to hide sensitive input)
    try:
        user_input = getpass.getpass('')  # Prompt already displayed
    except EOFError:
        user_input = ''
    
    # Print newline after input
    print('', flush=True, file=output_stream)
    
    return None, session_id, user_input


def handle_interactive_prompt(line: str,
                               output_stream: typing.Optional[typing.Any] = None
                              ) -> typing.Optional[str]:
    """Handle interactive SSH prompts in streamed logs (sync version).
    
    Args:
        line: The log line to check for interactive prompt markers.
        output_stream: The output stream to display prompts to.
    
    Returns:
        The line with the marker removed, or None if this was an interactive
        prompt (meaning the line was consumed and should not be printed).
    """
    processed_line, session_id, user_input = _process_prompt_and_get_input(
        line, output_stream)
    
    if session_id is None:
        return processed_line
    
    # Send to API server
    api_url = server_common.get_server_url()
    try:
        requests.post(
            f'{api_url}/api/interactive/{session_id}',
            json={'input': user_input},
            timeout=30,
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to send interactive input: {e}')
    
    return processed_line


async def handle_interactive_prompt_async(
        line: str,
        output_stream: typing.Optional[typing.Any] = None
) -> typing.Optional[str]:
    """Handle interactive SSH prompts in streamed logs (async version).
    
    Args:
        line: The log line to check for interactive prompt markers.
        output_stream: The output stream to display prompts to.
    
    Returns:
        The line with the marker removed, or None if this was an interactive
        prompt (meaning the line was consumed and should not be printed).
    """
    processed_line, session_id, user_input = _process_prompt_and_get_input(
        line, output_stream)
    
    if session_id is None:
        return processed_line
    
    # Send to API server
    api_url = server_common.get_server_url()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f'{api_url}/api/interactive/{session_id}',
                json={'input': user_input},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                pass
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to send interactive input: {e}')
    
    return processed_line
