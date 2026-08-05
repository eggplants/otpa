"""Tests for otpa.proto (protobuf wire codec)."""

from __future__ import annotations

import pytest

from otpa import proto
from otpa.proto import ProtoError


@pytest.mark.parametrize("value", [0, 1, 127, 128, 300, 16384, 2**32, 2**64 - 1])
def test_varint_round_trip(value):
    encoded = proto.encode_varint(value)
    decoded, pos = proto.decode_varint(encoded, 0)
    assert decoded == value
    assert pos == len(encoded)


def test_encode_negative_varint_raises():
    with pytest.raises(ProtoError):
        proto.encode_varint(-1)


def test_decode_truncated_varint_raises():
    with pytest.raises(ProtoError):
        proto.decode_varint(b"\x80\x80", 0)


def test_writer_round_trip_through_read_fields():
    buf = proto.Writer().length_delimited(1, b"secret").length_delimited(2, b"name").varint(4, 2).getvalue()
    fields = list(proto.read_fields(buf))
    assert (1, proto.WIRE_LEN, b"secret") in fields
    assert (2, proto.WIRE_LEN, b"name") in fields
    assert (4, proto.WIRE_VARINT, 2) in fields


def test_read_fields_skips_fixed_width():
    # field 5 (i32) then field 1 (varint=9): the i32 must be skipped cleanly.
    buf = b"\x2d\x01\x02\x03\x04" + proto.Writer().varint(1, 9).getvalue()
    fields = list(proto.read_fields(buf))
    assert (1, proto.WIRE_VARINT, 9) in fields


def test_read_fields_truncated_length_raises():
    with pytest.raises(ProtoError):
        list(proto.read_fields(b"\x0a\x05ab"))
