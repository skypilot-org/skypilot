"""Wire-compatibility tests for autostopv1 proto.

The PR2 migration renames proto fields
  - `hook` (field 5) -> `termination_hook`
  - `hook_timeout` (field 6) -> `termination_hook_timeout`
while keeping the field numbers unchanged. This keeps new clients and
old skylets interoperable at the wire level: bytes serialized under
either name decode to the same message on the other side.

These tests pin that contract. Revert-check: if someone changes the
field numbers, the hand-crafted wire payloads below decode to the
wrong field and the tests fail.
"""
from sky.schemas.generated import autostopv1_pb2


def _varint(value: int) -> bytes:
    """Encode an int as a protobuf varint."""
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0x7F)
    return bytes(out)


def _field_tag(field_number: int, wire_type: int) -> bytes:
    return _varint((field_number << 3) | wire_type)


def _encode_string_field(field_number: int, value: str) -> bytes:
    # wire type 2 = length-delimited
    data = value.encode()
    return _field_tag(field_number, 2) + _varint(len(data)) + data


def _encode_int32_field(field_number: int, value: int) -> bytes:
    # wire type 0 = varint
    return _field_tag(field_number, 0) + _varint(value)


def test_old_wire_bytes_decode_under_new_field_name():
    """Bytes produced by an old client (field 5 = hook) decode as
    termination_hook on a new server."""
    raw = (_encode_string_field(5, 'checkpoint.sh') +
           _encode_int32_field(6, 300))
    msg = autostopv1_pb2.SetAutostopRequest()
    msg.ParseFromString(raw)
    assert msg.HasField('termination_hook')
    assert msg.termination_hook == 'checkpoint.sh'
    assert msg.HasField('termination_hook_timeout')
    assert msg.termination_hook_timeout == 300


def test_new_wire_bytes_have_same_field_numbers():
    """A new client's serialized SetAutostopRequest uses field numbers
    5 and 6 (unchanged since PR1). Old binaries receiving this payload
    will find the fields at the historical positions."""
    msg = autostopv1_pb2.SetAutostopRequest(
        idle_minutes=0,
        backend='test',
        down=False,
        termination_hook='x',
        termination_hook_timeout=60,
    )
    blob = msg.SerializeToString()

    # Expect the termination_hook bytes to appear at field 5 (wire tag
    # `(5 << 3) | 2 = 42`) and termination_hook_timeout at field 6
    # (wire tag `(6 << 3) | 0 = 48`). If the numbers ever drift, these
    # asserts fail.
    tag_field5 = bytes([(5 << 3) | 2])  # 0x2A
    tag_field6 = bytes([(6 << 3) | 0])  # 0x30
    assert tag_field5 in blob
    assert tag_field6 in blob


def test_absent_fields_report_not_set():
    """When neither field is set the HasField accessor returns False,
    matching the optional-field semantics the skylet relies on."""
    msg = autostopv1_pb2.SetAutostopRequest(idle_minutes=-1, backend='test')
    assert not msg.HasField('termination_hook')
    assert not msg.HasField('termination_hook_timeout')
