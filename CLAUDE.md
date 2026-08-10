# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`otpa` converts between plain `otpauth://` (Key URI) links and Google Authenticator's
`otpauth-migration://offline?data=...` export links (the batched, protobuf-encoded format
produced by GA's "Export accounts" QR code). It's a small CLI + library with two required
dependencies (`qrcode` + `pypng`, for `--qr`) and otherwise no others.

## Commands

This project uses `uv` for env/deps and `mise` to run tasks (see `mise.toml` for the canonical
task definitions).

```bash
uv sync --all-groups          # install runtime + dev + docs dependency groups
uv run pytest                 # run the full test suite
uv run pytest tests/test_migration.py::test_otpauth_to_migration_splits_when_exceeding_batch_size  # single test
uv format                     # run ruff (formatting + lint fixes) — mise task `ruff`
uvx ty check --respect-ignore-files   # type check — mise task `ty`
uv run pymarkdown fix --list-files .  # markdown lint — mise task `pymarkdown`
uv build                      # build sdist/wheel
uv run pdoc otpa -o ./docs --docformat google  # generate API docs
```

Or via mise: `mise run pytest`, `mise run pytest-cov`, `mise run pre-commit` (ruff + ty +
pymarkdown + pyproject-fmt), `mise run ci` (pre-commit + pytest-cov) — this is what CI runs.

The venv is tied to the absolute repo path (`uv sync` bakes it into script shebangs). If the
repo directory gets renamed/moved, delete `.venv/` and `uv sync` again rather than debugging
"No such file or directory" / `ModuleNotFoundError` — it's a stale interpreter path, not a code
bug.

Only two required dependencies (`qrcode` + `pypng` in `pyproject.toml`'s `dependencies`, used only
by `--qr`/`_build_qr_html`) — protobuf encoding/decoding itself is still hand-rolled specifically
to avoid a `protobuf`/`protoc` dependency for this one fixed schema. Keep it that way; don't reach
for a real protobuf library or add other required deps without a strong reason.

## Architecture

Four modules, each with a single responsibility, layered as: CLI → migration (link parsing &
protobuf mapping) → proto (raw wire codec) → models (plain dataclasses/enums).

- **`otpa/proto.py`** — minimal protobuf wire-format codec (`Writer` for encoding,
  `read_fields()` for decoding). Only implements varints and length-delimited fields, the two
  wire types the migration schema actually uses; fixed32/64 fields are skipped, not decoded.
  Has no knowledge of the migration schema itself — it's a generic varint/length-delimited
  reader/writer.
- **`otpa/models.py`** — plain dataclasses/enums mirroring the migration protobuf schema
  (`OtpParameters`, `MigrationPayload`, `Algorithm`, `DigitCount`, `OtpType`). No behavior beyond
  small label/parsing helpers on the enums.
- **`otpa/migration.py`** — the core conversion logic and the schema-specific field-number
  mapping. Two independent directions:
  - `otpauth-migration://` link → payload (`migration_link_to_payload`) → list of `otpauth://`
    links (`migration_to_otpauth`).
  - one or more `otpauth://` links → `OtpParameters` list → `otpauth-migration://` link(s)
    (`otpauth_to_migration`, thin wrapper around `params_to_migration`). When the number of
    accounts exceeds `batch_size` (CLI: `-n`/`--batch-size`, default `DEFAULT_BATCH_SIZE = 10`),
    the accounts are split across multiple output links, each with a correctly set
    `batch_size`/`batch_index`, sharing the same `batch_id`.
  - `duplicate_groups(params)` groups account indices sharing the same `secret`/`issuer` — used
    by the CLI's `--dedupe`; it has no interactive behavior itself (no I/O), just index grouping.
  - All parse/convert failures raise `ConversionError` (a `ValueError` subclass) — this is the
    one error type the CLI layer catches and turns into a clean `error: ...` message + exit 1.
- **`otpa/cli.py`** — argparse-based subcommand dispatch (`cm`, `ca`, `i`), thin wrappers around
  `migration.py` functions. `cm` and `ca` both take positional links and/or `-f`/`--file` (which
  may repeat), and both accept any mix of `otpauth://` and `otpauth-migration://` inputs — each
  link is parsed by scheme (`_parse_any_link`/`_gather_params`) and flattened into one
  `OtpParameters` list regardless of its original scheme, so mixed input just merges accounts;
  the subcommand alone decides the *output* scheme: `cm` always encodes to one or more
  `otpauth-migration://` links (only `cm` has `-n`/`--batch-size`, since batching is
  meaningless once already decoded to `otpauth://`), `ca` always decodes to `otpauth://` links.
  `-d`/`--dedupe` (prompts) or `-D`/`--dedupe-with-default` (auto-keeps the first in each group,
  mutually exclusive with `-d`) resolve `duplicate_groups()` before printing, then
  `-r`/`--rename-empty-issuer` prompts for accounts still missing an issuer — both shared by `cm`
  and `ca` via `_apply_interactive_options`. `-o`/`--out`/`-O`/`--overwrite` (also shared, plus on
  `i`) redirect stdout to a file for the duration of the command via `contextlib.redirect_stdout`;
  interactive prompts still go to the real stderr/stdin since only stdout is redirected. `cm` and
  `ca` also both share `--qr` (routed through `_emit_links`): instead of printing the output
  links, `_build_qr_html` renders each as a PNG QR code (via
  `qrcode.make(..., image_factory=PyPNGImage)`, base64-embedded, no Pillow needed) into one
  paginated `[i/n]`/prev/next HTML page — the `[i/n]` counter is itself a link to that page's
  underlying URL — which `_show_qr_codes` writes to a `tempfile.mkstemp` file and opens with
  `webbrowser.open_new_tab`. `qrcode`/`PyPNGImage` are imported at module top-level (they're
  required dependencies, not optional). `i` prints human-readable info for either link type via
  `detect_scheme`.

When adding a new migration protobuf field, thread it through in this order: `_F_*` constant in
`migration.py` → field on the matching dataclass in `models.py` → read/write branch in
`_parse_params`/`_serialize_params` (or `parse_payload`/`serialize_payload`) in `migration.py`.

## Testing conventions

Tests mirror the module split 1:1: `tests/test_proto.py`, `tests/test_migration.py`, `tests/test_cli.py`. 
