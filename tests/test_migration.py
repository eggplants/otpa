"""Tests for otpa.migration (link conversions)."""

from __future__ import annotations

import pytest

from otpa import migration
from otpa.migration import ConversionError
from otpa.models import Algorithm, DigitCount, OtpType

# Canonical vector from dim13/otpauth's TestConvert.
MIGRATION_LINK = "otpauth-migration://offline?data=CjEKCkhlbGxvId6tvu8SGEV4YW1wbGU6YWxpY2VAZ29vZ2xlLmNvbRoHRXhhbXBsZTAC"
SECRET_B32 = "JBSWY3DPEHPK3PXP"


def test_migration_to_otpauth_matches_reference():
    urls = migration.migration_to_otpauth(MIGRATION_LINK)
    assert urls == [
        "otpauth://totp/Example:alice@google.com?secret=JBSWY3DPEHPK3PXP&issuer=Example&period=30",
    ]


def test_migration_payload_fields():
    payload = migration.migration_link_to_payload(MIGRATION_LINK)
    assert len(payload.otp_parameters) == 1
    op = payload.otp_parameters[0]
    assert op.name == "Example:alice@google.com"
    assert op.issuer == "Example"
    assert op.type is OtpType.TOTP
    assert migration.encode_secret(op.secret) == SECRET_B32


def test_secret_round_trip():
    raw = migration.decode_secret(SECRET_B32)
    assert migration.encode_secret(raw) == SECRET_B32
    assert raw == b"Hello!\xde\xad\xbe\xef"


def test_decode_secret_accepts_lowercase_and_spaces():
    assert migration.decode_secret("jbsw y3dp ehpk 3pxp") == b"Hello!\xde\xad\xbe\xef"


def test_decode_secret_invalid_raises():
    with pytest.raises(ConversionError):
        migration.decode_secret("not base32!!!")


def test_otpauth_to_migration_round_trips():
    urls = migration.migration_to_otpauth(MIGRATION_LINK)
    rebuilt_links = migration.otpauth_to_migration(urls)
    assert len(rebuilt_links) == 1
    assert migration.migration_to_otpauth(rebuilt_links[0]) == urls


def test_otpauth_link_to_params_hotp_counter():
    op = migration.otpauth_link_to_params(
        "otpauth://hotp/Acme:bob?secret=JBSWY3DPEHPK3PXP&issuer=Acme&counter=42",
    )
    assert op.type is OtpType.HOTP
    assert op.counter == 42
    assert op.issuer == "Acme"


def test_otpauth_link_to_params_infers_issuer_from_label():
    op = migration.otpauth_link_to_params("otpauth://totp/Foo:carol?secret=JBSWY3DPEHPK3PXP")
    assert op.issuer == "Foo"


def test_params_to_otpauth_link_encodes_digits_and_algorithm():
    op = migration.otpauth_link_to_params(
        "otpauth://totp/Svc:dave?secret=JBSWY3DPEHPK3PXP&algorithm=SHA256&digits=8",
    )
    assert op.algorithm is Algorithm.SHA256
    assert op.digits is DigitCount.EIGHT
    link = migration.params_to_otpauth_link(op)
    assert "algorithm=SHA256" in link
    assert "digits=8" in link


def test_hotp_migration_round_trip_preserves_counter():
    links = migration.otpauth_to_migration(
        ["otpauth://hotp/Acme:bob?secret=JBSWY3DPEHPK3PXP&issuer=Acme&counter=7"],
    )
    payload = migration.migration_link_to_payload(links[0])
    assert payload.otp_parameters[0].counter == 7
    assert payload.otp_parameters[0].type is OtpType.HOTP


def test_otpauth_to_migration_bundles_multiple():
    urls = [
        "otpauth://totp/A:a?secret=JBSWY3DPEHPK3PXP&issuer=A",
        "otpauth://totp/B:b?secret=JBSWY3DPEHPK3PXP&issuer=B",
    ]
    links = migration.otpauth_to_migration(urls)
    assert len(links) == 1
    payload = migration.migration_link_to_payload(links[0])
    assert len(payload.otp_parameters) == 2
    assert payload.batch_size == 1
    assert payload.batch_index == 0


def _make_otpauth_link(i: int) -> str:
    return f"otpauth://totp/Svc:user{i}?secret=JBSWY3DPEHPK3PXP&issuer=Svc"


def test_otpauth_to_migration_default_batch_size_is_ten():
    assert migration.DEFAULT_BATCH_SIZE == 10


def test_otpauth_to_migration_splits_when_exceeding_batch_size():
    urls = [_make_otpauth_link(i) for i in range(25)]
    links = migration.otpauth_to_migration(urls, batch_size=10)
    assert len(links) == 3
    counts = []
    for index, link in enumerate(links):
        payload = migration.migration_link_to_payload(link)
        assert payload.batch_size == 3
        assert payload.batch_index == index
        counts.append(len(payload.otp_parameters))
    assert counts == [10, 10, 5]


def test_otpauth_to_migration_shares_batch_id_across_split_links():
    urls = [_make_otpauth_link(i) for i in range(15)]
    links = migration.otpauth_to_migration(urls, batch_id=99, batch_size=10)
    assert len(links) == 2
    for link in links:
        payload = migration.migration_link_to_payload(link)
        assert payload.batch_id == 99


def test_otpauth_to_migration_no_split_when_within_batch_size():
    urls = [_make_otpauth_link(i) for i in range(10)]
    links = migration.otpauth_to_migration(urls, batch_size=10)
    assert len(links) == 1


def test_otpauth_to_migration_invalid_batch_size_raises():
    with pytest.raises(ConversionError):
        migration.otpauth_to_migration([_make_otpauth_link(0)], batch_size=0)


def test_migration_link_wrong_scheme_raises():
    with pytest.raises(ConversionError):
        migration.migration_link_to_payload("otpauth://totp/x?secret=AA")


def test_migration_link_missing_data_raises():
    with pytest.raises(ConversionError):
        migration.migration_link_to_payload("otpauth-migration://offline?foo=bar")


def test_otpauth_link_missing_secret_raises():
    with pytest.raises(ConversionError):
        migration.otpauth_link_to_params("otpauth://totp/x?issuer=y")


def test_otpauth_link_wrong_scheme_raises():
    with pytest.raises(ConversionError):
        migration.otpauth_link_to_params("otpauth-migration://offline?data=AA")


def test_detect_scheme():
    assert migration.detect_scheme("otpauth://totp/x") == "otpauth"
    assert migration.detect_scheme("otpauth-migration://offline?data=AA") == "otpauth-migration"


def test_duplicate_groups_finds_matching_secret_and_issuer():
    params = [
        migration.otpauth_link_to_params("otpauth://totp/Svc:a?secret=JBSWY3DPEHPK3PXP&issuer=Svc"),
        migration.otpauth_link_to_params("otpauth://totp/Svc:b?secret=JBSWY3DPEHPK3PXP&issuer=Svc"),
        migration.otpauth_link_to_params("otpauth://totp/Other:c?secret=JBSWY3DPEHPK3PXP&issuer=Other"),
    ]
    assert migration.duplicate_groups(params) == [[0, 1]]


def test_duplicate_groups_empty_when_no_matches():
    params = [
        migration.otpauth_link_to_params("otpauth://totp/A:a?secret=JBSWY3DPEHPK3PXP&issuer=A"),
        migration.otpauth_link_to_params("otpauth://totp/B:b?secret=JBSWY3DPEHPK3PXP&issuer=B"),
    ]
    assert migration.duplicate_groups(params) == []
