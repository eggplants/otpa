"""Minimal Protocol Buffers wire-format codec.

Only the pieces needed to (de)serialize the Google Authenticator migration
payload are implemented: varints and length-delimited fields. This avoids a
dependency on ``protobuf`` / ``protoc`` for such a small, fixed schema.

See https://protobuf.dev/programming-guides/encoding/ for the wire format.
"""

from __future__ import annotations

from collections.abc import Iterator

WIRE_VARINT = 0
WIRE_LEN = 2
WIRE_I64 = 1
WIRE_I32 = 5


class ProtoError(ValueError):
    """Raised when a protobuf buffer is malformed or truncated."""


def encode_varint(value: int) -> bytes:
    """Encode a non-negative integer as a base-128 varint."""
    if value < 0:
        msg = f"cannot encode negative varint: {value}"
        raise ProtoError(msg)
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def decode_varint(buf: bytes, pos: int) -> tuple[int, int]:
    """Decode a varint from ``buf`` at ``pos``; return ``(value, new_pos)``."""
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            msg = "truncated varint"
            raise ProtoError(msg)
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


class Writer:
    """Accumulates protobuf-encoded fields into a byte buffer."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def varint(self, field_number: int, value: int) -> Writer:
        """Append a varint field (int32/int64/uint64/enum/bool)."""
        self._buf += encode_varint(field_number << 3 | WIRE_VARINT)
        self._buf += encode_varint(value)
        return self

    def length_delimited(self, field_number: int, data: bytes) -> Writer:
        """Append a length-delimited field (bytes/string/embedded message)."""
        self._buf += encode_varint(field_number << 3 | WIRE_LEN)
        self._buf += encode_varint(len(data))
        self._buf += data
        return self

    def getvalue(self) -> bytes:
        """Return the accumulated buffer."""
        return bytes(self._buf)


def read_fields(buf: bytes) -> Iterator[tuple[int, int, object]]:
    """Iterate ``(field_number, wire_type, value)`` triples from ``buf``.

    Varint fields yield an ``int``; length-delimited fields yield ``bytes``.
    Fixed 32/64-bit fields are skipped transparently (unused by this schema).
    """
    pos = 0
    size = len(buf)
    while pos < size:
        tag, pos = decode_varint(buf, pos)
        field_number = tag >> 3
        wire_type = tag & 0x7
        if wire_type == WIRE_VARINT:
            value, pos = decode_varint(buf, pos)
            yield field_number, wire_type, value
        elif wire_type == WIRE_LEN:
            length, pos = decode_varint(buf, pos)
            end = pos + length
            if end > size:
                msg = "truncated length-delimited field"
                raise ProtoError(msg)
            yield field_number, wire_type, buf[pos:end]
            pos = end
        elif wire_type == WIRE_I64:
            pos += 8
        elif wire_type == WIRE_I32:
            pos += 4
        else:
            msg = f"unsupported wire type: {wire_type}"
            raise ProtoError(msg)
