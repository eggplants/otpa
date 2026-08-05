# otpa

[![PyPI version](
  <https://badge.fury.io/py/otpa.svg>
  )](
  <https://badge.fury.io/py/otpa>
) [![CI](
  <https://github.com/eggplants/otpa/actions/workflows/ci.yml/badge.svg>
  )](
  <https://github.com/eggplants/otpa/actions/workflows/ci.yml>
)

Convert between plain [`otpauth://`](https://github.com/google/google-authenticator/wiki/Key-Uri-Format) links and Google Authenticator `otpauth-migration://offline?data=...` export links.

Inspired by [dim13/otpauth](https://github.com/dim13/otpauth).

## Install

```bash
pipx install otpa
# or
pip install otpa
```

## CLI

```bash
# -> otpauth-migration:// link(s)
otpa cm "otpauth-migration://offline?data=..." "otpauth://totp/ACME:alice?secret=JBSWY3DPEHPK3PXP&issuer=ACME"
otpa cm -f otpauth-links.txt             # read links from a file
otpa cm -f otpauth-links.txt -n 5        # same as --batch-size 5 (cm only)
otpa cm -f a.txt -f b.txt link1 link2    # -f may repeat, combined with positional links

# -> otpauth:// links
otpa ca "otpauth-migration://offline?data=..." ["otpauth-migration://..." ...]
otpa ca -f migration-links.txt

# interactively resolve accounts sharing the same secret/issuer
otpa cm -d "otpauth-migration://offline?data=..." ...
otpa ca -d "otpauth-migration://offline?data=..." ...

# open each output link as a QR code in your browser instead of printing them
otpa cm --qr "otpauth://totp/ACME:alice?secret=JBSWY3DPEHPK3PXP&issuer=ACME"
otpa ca --qr "otpauth-migration://offline?data=..."

# inspect either kind of link
otpa i "otpauth-migration://offline?data=..."
otpa i "otpauth://totp/Example?secret=JBSWY3DPEHPK3PXP"
```

`-f`/`--file` reads one link per line; blank lines and `#` comments are ignored.

### Example

```console
$ otpa ca "otpauth-migration://offline?data=CjEKCkhlbGxvId6tvu8SGEV4YW1wbGU6YWxpY2VAZ29vZ2xlLmNvbRoHRXhhbXBsZTAC"
otpauth://totp/Example:alice@google.com?secret=JBSWY3DPEHPK3PXP&issuer=Example&period=30
```

## Library

```python
from otpa import migration

# Expand a migration batch into otpauth:// links.
urls = migration.migration_to_otpauth("otpauth-migration://offline?data=...")

# Bundle otpauth:// links into one or more migration links
# (split across links when count > batch_size, default 10).
links = migration.otpauth_to_migration([
    "otpauth://totp/Example:alice@google.com?secret=JBSWY3DPEHPK3PXP&issuer=Example",
], batch_size=10)

# Lower-level access to the decoded protobuf payload.
payload = migration.migration_link_to_payload("otpauth-migration://offline?data=...")
for op in payload.otp_parameters:
    print(op.name, op.issuer, migration.encode_secret(op.secret))
```
