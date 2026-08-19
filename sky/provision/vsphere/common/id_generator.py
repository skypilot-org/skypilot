"""ID Generator
"""

import random
import string
import uuid
from typing import Optional


def generate_random_uuid() -> str:
    """Generate a random UUID string.

    Returns:
        A string representation of a randomly generated UUID.
    """
    return str(uuid.uuid4())


def rand(value: str) -> str:
    """Append a random string to the given value.

    Args:
        value: The base string to append random characters to.

    Returns:
        The value concatenated with a 5-character random uppercase string.
    """
    return value + generate_random_string(5)


def generate_random_string(length: int) -> str:
    """Generate a random uppercase string of specified length.

    Args:
        length: The number of characters in the generated string.

    Returns:
        A random string of uppercase ASCII letters.
    """
    if length < 0:
        raise ValueError(f'Length must be non-negative, got {length}')
    return ''.join(random.choice(string.ascii_uppercase) for _ in range(length))
