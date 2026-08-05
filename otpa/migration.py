"""Convert between ``otpauth://`` and ``otpauth-migration://`` links.

Two directions are supported:

* ``otpauth-migration://offline?data=...`` (a Google Authenticator export
  batch, protobuf-encoded) -> one or more ``otpauth://`` account links.
* one or more ``otpauth://`` links -> one or more ``otpauth-migration://``
  batches (split when the account count exceeds ``batch_size``).

The protobuf schema is encoded/decoded by hand via :mod:`otpa.proto`.
"""

from __future__ import annotations

import base64
import binascii
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from otpa.models import Algorithm, DigitCount, MigrationPayload, OtpParameters, OtpType
from otpa.proto import Writer, read_fields

MIGRATION_SCHEME = "otpauth-migration"
MIGRATION_HOST = "offline"
OTPAUTH_SCHEME = "otpauth"

# Protobuf field numbers (see migration.proto).
_F_OTP_PARAMETERS = 1
_F_VERSION = 2
_F_BATCH_SIZE = 3
_F_BATCH_INDEX = 4
_F_BATCH_ID = 5

_F_SECRET = 1
_F_NAME = 2
_F_ISSUER = 3
_F_ALGORITHM = 4
_F_DIGITS = 5
_F_TYPE = 6
_F_COUNTER = 7
_F_UNIQUE_ID = 8

_DEFAULT_PERIOD = 30
DEFAULT_BATCH_SIZE = 10


class ConversionError(ValueError):
    """Raised when a link cannot be parsed or converted."""


# base32 secret helpers
def encode_secret(secret: bytes) -> str:
    """Encode raw secret bytes as an unpadded, uppercase base32 string."""
    return base64.b32encode(secret).decode("ascii").rstrip("=")


def decode_secret(secret: str) -> bytes:
    """Decode a base32 ``secret`` (case-insensitive, padding optional)."""
    cleaned = secret.strip().replace(" ", "").upper()
    padding = (-len(cleaned)) % 8
    try:
        return base64.b32decode(cleaned + "=" * padding)
    except (binascii.Error, ValueError) as exc:
        msg = f"invalid base32 secret: {secret!r}"
        raise ConversionError(msg) from exc


# protobuf (de)serialization
def _serialize_params(op: OtpParameters) -> bytes:
    writer = Writer()
    if op.secret:
        writer.length_delimited(_F_SECRET, op.secret)
    if op.name:
        writer.length_delimited(_F_NAME, op.name.encode("utf-8"))
    if op.issuer:
        writer.length_delimited(_F_ISSUER, op.issuer.encode("utf-8"))
    if op.algorithm:
        writer.varint(_F_ALGORITHM, int(op.algorithm))
    if op.digits:
        writer.varint(_F_DIGITS, int(op.digits))
    if op.type:
        writer.varint(_F_TYPE, int(op.type))
    if op.counter:
        writer.varint(_F_COUNTER, op.counter)
    if op.unique_id:
        writer.length_delimited(_F_UNIQUE_ID, op.unique_id.encode("utf-8"))
    return writer.getvalue()


def _parse_params(buf: bytes) -> OtpParameters:
    op = OtpParameters(secret=b"")
    for field_number, _wire, value in read_fields(buf):
        if field_number == _F_SECRET and isinstance(value, bytes):
            op.secret = value
        elif field_number == _F_NAME and isinstance(value, bytes):
            op.name = value.decode("utf-8", "replace")
        elif field_number == _F_ISSUER and isinstance(value, bytes):
            op.issuer = value.decode("utf-8", "replace")
        elif field_number == _F_ALGORITHM and isinstance(value, int):
            op.algorithm = Algorithm(value) if value in Algorithm._value2member_map_ else Algorithm.SHA1
        elif field_number == _F_DIGITS and isinstance(value, int):
            op.digits = DigitCount(value) if value in DigitCount._value2member_map_ else DigitCount.SIX
        elif field_number == _F_TYPE and isinstance(value, int):
            op.type = OtpType(value) if value in OtpType._value2member_map_ else OtpType.TOTP
        elif field_number == _F_COUNTER and isinstance(value, int):
            op.counter = value
        elif field_number == _F_UNIQUE_ID and isinstance(value, bytes):
            op.unique_id = value.decode("utf-8", "replace")
    return op


def serialize_payload(payload: MigrationPayload) -> bytes:
    """Serialize a :class:`MigrationPayload` to protobuf bytes."""
    writer = Writer()
    for op in payload.otp_parameters:
        writer.length_delimited(_F_OTP_PARAMETERS, _serialize_params(op))
    if payload.version:
        writer.varint(_F_VERSION, payload.version)
    if payload.batch_size:
        writer.varint(_F_BATCH_SIZE, payload.batch_size)
    if payload.batch_index:
        writer.varint(_F_BATCH_INDEX, payload.batch_index)
    if payload.batch_id:
        writer.varint(_F_BATCH_ID, payload.batch_id)
    return writer.getvalue()


def parse_payload(buf: bytes) -> MigrationPayload:
    """Parse protobuf bytes into a :class:`MigrationPayload`."""
    payload = MigrationPayload(version=0, batch_size=0)
    for field_number, _wire, value in read_fields(buf):
        if field_number == _F_OTP_PARAMETERS and isinstance(value, bytes):
            payload.otp_parameters.append(_parse_params(value))
        elif field_number == _F_VERSION and isinstance(value, int):
            payload.version = value
        elif field_number == _F_BATCH_SIZE and isinstance(value, int):
            payload.batch_size = value
        elif field_number == _F_BATCH_INDEX and isinstance(value, int):
            payload.batch_index = value
        elif field_number == _F_BATCH_ID and isinstance(value, int):
            payload.batch_id = value
    return payload


# migration link <-> payload
def migration_link_to_payload(link: str) -> MigrationPayload:
    """Decode an ``otpauth-migration://offline?data=...`` link to a payload."""
    parsed = urlparse(link.strip())
    if parsed.scheme != MIGRATION_SCHEME:
        msg = f"not an {MIGRATION_SCHEME} link: {parsed.scheme!r}"
        raise ConversionError(msg)
    if parsed.netloc != MIGRATION_HOST:
        msg = f"unexpected host {parsed.netloc!r} (want {MIGRATION_HOST!r})"
        raise ConversionError(msg)
    data_values = parse_qs(parsed.query).get("data")
    if not data_values:
        msg = "migration link has no 'data' parameter"
        raise ConversionError(msg)
    # parse_qs already percent-decodes; restore '+' that a decoder may have
    # turned into a space, then base64-decode.
    raw = data_values[0].replace(" ", "+")
    try:
        decoded = base64.b64decode(raw)
    except (binascii.Error, ValueError) as exc:
        msg = "migration 'data' is not valid base64"
        raise ConversionError(msg) from exc
    return parse_payload(decoded)


def payload_to_migration_link(payload: MigrationPayload) -> str:
    """Encode a payload as an ``otpauth-migration://offline?data=...`` link."""
    data = base64.b64encode(serialize_payload(payload)).decode("ascii")
    query = urlencode({"data": data})
    return f"{MIGRATION_SCHEME}://{MIGRATION_HOST}?{query}"


# otpauth link <-> otp parameters
def params_to_otpauth_link(op: OtpParameters) -> str:
    """Build an ``otpauth://`` link from a single :class:`OtpParameters`."""
    query: dict[str, str] = {"secret": encode_secret(op.secret)}
    if op.issuer:
        query["issuer"] = op.issuer
    if op.algorithm not in (Algorithm.UNSPECIFIED, Algorithm.SHA1):
        query["algorithm"] = op.algorithm.label
    if op.digits is DigitCount.EIGHT:
        query["digits"] = str(op.digits.count)
    if op.type is OtpType.HOTP:
        query["counter"] = str(op.counter)
    else:
        query["period"] = str(_DEFAULT_PERIOD)
    # Keep ':' and '@' unencoded, matching the Key-Uri "Issuer:account" label.
    label = quote(op.name, safe="@:")
    return f"{OTPAUTH_SCHEME}://{op.type.label}/{label}?{urlencode(query)}"


def otpauth_link_to_params(link: str) -> OtpParameters:
    """Parse an ``otpauth://`` link into an :class:`OtpParameters`."""
    parsed = urlparse(link.strip())
    if parsed.scheme != OTPAUTH_SCHEME:
        msg = f"not an {OTPAUTH_SCHEME} link: {parsed.scheme!r}"
        raise ConversionError(msg)
    otp_type = OtpType.from_label(parsed.netloc)
    # urlparse leaves the label percent-encoded in .path; decode it here.
    name = unquote(parsed.path.lstrip("/"))
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    if "secret" not in query:
        msg = "otpauth link has no 'secret' parameter"
        raise ConversionError(msg)
    issuer = query.get("issuer", "")
    if not issuer and ":" in name:
        issuer = name.split(":", 1)[0]
    counter = 0
    if "counter" in query:
        try:
            counter = int(query["counter"])
        except ValueError as exc:
            msg = f"invalid counter: {query['counter']!r}"
            raise ConversionError(msg) from exc
    digits = DigitCount.SIX
    if "digits" in query:
        try:
            digits = DigitCount.from_count(int(query["digits"]))
        except ValueError as exc:
            msg = f"invalid digits: {query['digits']!r}"
            raise ConversionError(msg) from exc
    return OtpParameters(
        secret=decode_secret(query["secret"]),
        name=name,
        issuer=issuer,
        algorithm=Algorithm.from_label(query.get("algorithm", "")),
        digits=digits,
        type=otp_type,
        counter=counter,
    )


# high-level helpers used by the CLI
def migration_to_otpauth(link: str) -> list[str]:
    """Expand one migration link into its list of ``otpauth://`` links."""
    payload = migration_link_to_payload(link)
    return [params_to_otpauth_link(op) for op in payload.otp_parameters]


def params_to_migration(
    params: list[OtpParameters],
    *,
    batch_id: int = 0,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[str]:
    """Bundle already-parsed accounts into one or more migration links.

    When more than ``batch_size`` accounts are given, they are split across
    multiple ``otpauth-migration://`` links, each holding up to
    ``batch_size`` accounts.
    """
    if batch_size < 1:
        msg = f"batch_size must be >= 1 (got {batch_size})"
        raise ConversionError(msg)
    chunks = [params[i : i + batch_size] for i in range(0, len(params), batch_size)] or [[]]
    total = len(chunks)
    return [
        payload_to_migration_link(
            MigrationPayload(
                otp_parameters=chunk,
                version=1,
                batch_size=total,
                batch_index=index,
                batch_id=batch_id,
            ),
        )
        for index, chunk in enumerate(chunks)
    ]


def otpauth_to_migration(links: list[str], *, batch_id: int = 0, batch_size: int = DEFAULT_BATCH_SIZE) -> list[str]:
    """Bundle ``otpauth://`` links into one or more migration links.

    When more than ``batch_size`` links are given, they are split across
    multiple ``otpauth-migration://`` links, each holding up to
    ``batch_size`` accounts.
    """
    params = [otpauth_link_to_params(link) for link in links]
    return params_to_migration(params, batch_id=batch_id, batch_size=batch_size)


def duplicate_groups(params: list[OtpParameters]) -> list[list[int]]:
    """Group indices of ``params`` entries sharing the same ``secret``/``issuer``.

    Returns one list of indices (into ``params``) per group of two or more
    matching entries; entries with no duplicate are omitted entirely. Order
    of groups, and of indices within a group, follows ``params``.
    """
    groups: dict[tuple[bytes, str], list[int]] = {}
    for i, op in enumerate(params):
        groups.setdefault((op.secret, op.issuer), []).append(i)
    return [indices for indices in groups.values() if len(indices) > 1]


def detect_scheme(link: str) -> str:
    """Return the URL scheme of ``link`` (lower-cased)."""
    return urlparse(link.strip()).scheme.lower()
