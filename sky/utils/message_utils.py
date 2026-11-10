"""Utilities for encoding and decoding messages."""
import json
import re
import typing
from typing import Any, Literal, Optional, Tuple, Union

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


@typing.overload
def decode_payload(payload_str: str,
                   payload_type: Optional[str] = None,
                   raise_for_mismatch: Literal[True] = True) -> Any:
    ...


@typing.overload
def decode_payload(
        payload_str: str,
        payload_type: Optional[str] = None,
        raise_for_mismatch: Literal[False] = False) -> Tuple[bool, Any]:
    ...


def decode_payload(
        payload_str: str,
        payload_type: Optional[str] = None,
        raise_for_mismatch: bool = True) -> Union[Tuple[bool, Any], Any]:
    """Decode a payload string.

    Args:
        payload_str: A string that is encoded from a payload.
        payload_type: The type of the payload.
        raise_for_mismatch: Whether to raise an error if the payload string is
            not valid.

    Returns:
        A tuple of (bool, Any). The bool indicates whether it is a payload
        string. The Any is the decoded payload, which is a str, dict or list.

        With `raise_for_mismatch=False` this is a question about arbitrary
        text, so it always answers: a payload-shaped line whose body will not
        parse is not a payload, and comes back as (False, the whole original
        line) for the caller to treat as content.
    """
    matched = _PAYLOAD_PATTERN.findall(payload_str)
    if not matched:
        if raise_for_mismatch:
            raise ValueError(f'Invalid payload string: \n{payload_str}')
        else:
            return False, payload_str

    for payload_type_str, body_str in matched:
        if payload_type is None or payload_type == payload_type_str:
            if raise_for_mismatch:
                return json.loads(body_str)
            try:
                decoded = json.loads(body_str)
            except (ValueError, RecursionError):
                # A question, so it must answer rather than raise: the log
                # streamer asks it of every line a task wrote, and task
                # output can be payload-shaped by accident. Raising escaped
                # the streaming generator and truncated the log. Broad on
                # purpose -- bad syntax, the recursion limit and the integer
                # digit limit are only the failures we know of.
                return False, payload_str
            return True, decoded

    if raise_for_mismatch:
        raise ValueError(f'Invalid payload string: \n{payload_str}')
    else:
        return False, payload_str
