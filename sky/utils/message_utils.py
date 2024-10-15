"""Utilities for encoding and decoding messages."""
import json
import re
from typing import Any, Optional

_PAYLOAD_PATTERN = re.compile(r'<sky-payload(.*?)>(.*?)</sky-payload>')
_PAYLOAD_STR = '<sky-payload{type}>{content}</sky-payload>\n'


def encode_payload(payload: Any, payload_type: Optional[str] = None) -> str:
    """Encode a payload to make it more robust for parsing.

    This makes message transfer more robust to any additional strings added to
    the message during transfer.

    An example message that is polluted by the system warning:
    "LC_ALL: cannot change locale (en_US.UTF-8)\n<sky-payload>hello, world</sky-payload>" # pylint: disable=line-too-long

    Args:
        payload: A str, dict or list to be encoded.

    Returns:
        A string that is encoded from the payload.
    """
    payload_str = json.dumps(payload)
    if payload_type is None:
        payload_type = ''
    payload_str = _PAYLOAD_STR.format(type=payload_type, content=payload_str)
    return payload_str


def decode_payload(payload_str: str,
                   payload_type: Optional[str] = None,
                   raise_for_mismatch: bool = True) -> Any:
    """Decode a payload string.

    Args:
        payload_str: A string that is encoded from a payload.

    Returns:
        A str, dict or list that is decoded from the payload string.
    """
    matched = _PAYLOAD_PATTERN.findall(payload_str)
    if not matched:
        if raise_for_mismatch:
            raise ValueError(f'Invalid payload string: \n{payload_str}')
        else:
            return payload_str

    for payload_type_str, payload_str in matched:
        if payload_type is None or payload_type == payload_type_str:
            return json.loads(payload_str)
