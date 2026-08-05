"""Data models for OTP parameters and Google Authenticator migration payloads.

The enum values mirror the ``otpauth-migration`` protobuf schema used by
Google Authenticator (see ``migration.proto`` in the ``dim13/otpauth`` project).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Algorithm(IntEnum):
    """HMAC algorithm (protobuf ``Payload.OtpParameters.Algorithm``)."""

    UNSPECIFIED = 0
    SHA1 = 1
    SHA256 = 2
    SHA512 = 3
    MD5 = 4

    @property
    def label(self) -> str:
        """Name used in an ``otpauth://`` URL (``SHA1``, ``SHA256`` ...)."""
        return "SHA1" if self is Algorithm.UNSPECIFIED else self.name

    @classmethod
    def from_label(cls, label: str) -> Algorithm:
        """Parse an ``algorithm`` query value; unknown/empty maps to ``SHA1``."""
        try:
            return cls[label.strip().upper()]
        except KeyError:
            return cls.SHA1


class DigitCount(IntEnum):
    """Number of OTP digits (protobuf ``Payload.OtpParameters.DigitCount``)."""

    UNSPECIFIED = 0
    SIX = 1
    EIGHT = 2

    @property
    def count(self) -> int:
        """Digit count as an integer; defaults to ``6``."""
        return 8 if self is DigitCount.EIGHT else 6

    @classmethod
    def from_count(cls, count: int) -> DigitCount:
        """Map an integer digit count to the enum; ``8`` -> EIGHT, else SIX."""
        return cls.EIGHT if count == 8 else cls.SIX  # noqa: PLR2004


class OtpType(IntEnum):
    """OTP type (protobuf ``Payload.OtpParameters.OtpType``)."""

    UNSPECIFIED = 0
    HOTP = 1
    TOTP = 2

    @property
    def label(self) -> str:
        """Name used as the ``otpauth://`` host (``totp`` or ``hotp``)."""
        return "hotp" if self is OtpType.HOTP else "totp"

    @classmethod
    def from_label(cls, label: str) -> OtpType:
        """Parse an ``otpauth`` host; ``hotp`` -> HOTP, else TOTP."""
        return cls.HOTP if label.strip().lower() == "hotp" else cls.TOTP


@dataclass
class OtpParameters:
    """A single OTP account (one entry within a migration payload)."""

    secret: bytes
    name: str = ""
    issuer: str = ""
    algorithm: Algorithm = Algorithm.SHA1
    digits: DigitCount = DigitCount.SIX
    type: OtpType = OtpType.TOTP
    counter: int = 0
    unique_id: str = ""


@dataclass
class MigrationPayload:
    """A decoded ``otpauth-migration`` batch of OTP accounts."""

    otp_parameters: list[OtpParameters] = field(default_factory=list)
    version: int = 1
    batch_size: int = 1
    batch_index: int = 0
    batch_id: int = 0
