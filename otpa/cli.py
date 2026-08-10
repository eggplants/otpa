"""Command-line interface for converting OTP links.

Subcommands::

    otpa cm <link> ... [-f FILE ...]        # -> otpauth-migration:// link(s)
    otpa ca <link> ... [-f FILE ...]        # -> otpauth:// link(s)
    otpa i  <otpauth | otpauth-migration>   # human-readable info

``cm``/``ca`` accept any mix of positional links and ``-f``/``--file`` files
(each with one link per line; blank lines and ``#`` comments ignored) — inputs
may freely mix ``otpauth://`` and ``otpauth-migration://`` links, since the
output scheme is determined by the subcommand rather than the input. ``cm``
always outputs ``otpauth-migration://`` link(s); ``ca`` always outputs
``otpauth://`` links. ``-n``/``--batch-size`` only applies to ``cm`` (it
controls how accounts are split across output migration links).

Both ``cm`` and ``ca`` also accept: ``-d``/``--dedupe`` (interactively resolve
accounts sharing the same secret and issuer before printing) or
``-D``/``--dedupe-with-default`` (same, but auto-keep the first account in
each group without prompting; mutually exclusive with ``-d``); then
``-r``/``--rename-empty-issuer`` (interactively assign an issuer/name to any
account still left with an empty issuer, running after dedupe resolves).

``cm``, ``ca``, and ``i`` all accept ``-o``/``--out PATH`` (write to ``PATH``
instead of stdout; error if ``PATH`` already exists) or ``-O``/``--overwrite
PATH`` (same, but replace ``PATH`` if it already exists). ``-o`` and ``-O``
are mutually exclusive.

Both ``cm`` and ``ca`` also accept ``--qr``, which renders each output link as
a QR code in a small self-contained HTML page (one QR code per page, with
"[i/n]" — linking to that page's underlying link — and prev/next navigation)
written to a temporary file and opened in the default browser, instead of
printing the links to stdout.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import tempfile
import webbrowser
from contextlib import redirect_stdout
from pathlib import Path
from typing import TextIO

import qrcode
from qrcode.image.pure import PyPNGImage

from otpa import __version__, migration
from otpa.migration import ConversionError


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        out_path = args.out or args.overwrite
        if out_path is None:
            return args.func(args)
        with _open_output(out_path, overwrite=args.overwrite is not None) as f, redirect_stdout(f):
            return args.func(args)
    except (ConversionError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        print("\naborted", file=sys.stderr)
        return 130


def _add_output_args(parser: argparse.ArgumentParser) -> None:
    """Add the mutually exclusive ``-o``/``--out`` and ``-O``/``--overwrite`` options."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-o",
        "--out",
        metavar="PATH",
        help="write output to PATH instead of stdout; error if PATH already exists.",
    )
    group.add_argument(
        "-O",
        "--overwrite",
        metavar="PATH",
        help="like --out, but overwrite PATH if it already exists.",
    )


def _add_convert_args(parser: argparse.ArgumentParser) -> None:
    """Add the args shared by ``cm`` and ``ca``: links, ``-f``, dedupe, rename, output."""
    parser.add_argument("links", nargs="*", metavar="LINK", help="otpauth:// or otpauth-migration:// link(s).")
    parser.add_argument(
        "-f",
        "--file",
        dest="files",
        action="append",
        default=[],
        metavar="FILE",
        help="file with one link per line (blank lines / '#' comments ignored); may repeat.",
    )
    dedupe_group = parser.add_mutually_exclusive_group()
    dedupe_group.add_argument(
        "-d",
        "--dedupe",
        action="store_true",
        help="prompt to resolve accounts sharing the same secret/issuer before printing.",
    )
    dedupe_group.add_argument(
        "-D",
        "--dedupe-with-default",
        action="store_true",
        help="like --dedupe, but auto-keep the first account in each group without prompting.",
    )
    parser.add_argument(
        "-r",
        "--rename-empty-issuer",
        action="store_true",
        help="prompt for a new issuer/name for accounts with an empty issuer (runs after --dedupe resolves).",
    )
    parser.add_argument(
        "--qr",
        action="store_true",
        help="open each output link as a QR code in a paginated HTML page in your browser, instead of printing them.",
    )
    _add_output_args(parser)


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="otpa",
        description="Convert between otpauth:// and otpauth-migration:// links.",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_cm = sub.add_parser(
        "cm",
        help="Convert otpauth:// and/or otpauth-migration:// link(s) into otpauth-migration:// link(s).",
    )
    _add_convert_args(p_cm)
    p_cm.add_argument(
        "-n",
        "--batch-size",
        type=int,
        default=migration.DEFAULT_BATCH_SIZE,
        help="max accounts per otpauth-migration link, split across multiple links if exceeded "
        f"(default: {migration.DEFAULT_BATCH_SIZE}).",
    )
    p_cm.set_defaults(func=cmd_cm)

    p_ca = sub.add_parser(
        "ca",
        help="Convert otpauth:// and/or otpauth-migration:// link(s) into otpauth:// link(s).",
    )
    _add_convert_args(p_ca)
    p_ca.set_defaults(func=cmd_ca)

    p_i = sub.add_parser("i", help="Print info about an otpauth or otpauth-migration link.")
    p_i.add_argument("link", metavar="LINK", help="otpauth:// or otpauth-migration:// link.")
    _add_output_args(p_i)
    p_i.set_defaults(func=cmd_info)

    return parser


def _open_output(path: str, *, overwrite: bool) -> TextIO:
    """Open ``path`` for writing, refusing to clobber an existing file unless ``overwrite``."""
    p = Path(path)
    if p.is_dir():
        msg = f"cannot write output: {path!r} is a directory"
        raise ConversionError(msg)
    if p.exists() and not overwrite:
        msg = f"output file already exists: {path!r} (use -O/--overwrite to replace it)"
        raise ConversionError(msg)
    try:
        return p.open("w", encoding="utf-8")
    except OSError as exc:
        msg = f"cannot write output to {path!r}: {exc}"
        raise ConversionError(msg) from exc


def _read_links(path: str) -> list[str]:
    """Read links from ``path``, one per line, skipping blanks and ``#`` comments."""
    text = Path(path).read_text(encoding="utf-8")
    links = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            links.append(line)
    if not links:
        msg = f"no links found in {path}"
        raise ValueError(msg)
    return links


def _parse_any_link(link: str) -> list[migration.OtpParameters]:
    """Parse ``link`` as either an ``otpauth://`` or ``otpauth-migration://`` link."""
    scheme = migration.detect_scheme(link)
    if scheme == migration.OTPAUTH_SCHEME:
        return [migration.otpauth_link_to_params(link)]
    if scheme == migration.MIGRATION_SCHEME:
        return migration.migration_link_to_payload(link).otp_parameters
    msg = f"unrecognized link scheme: {scheme!r}"
    raise ConversionError(msg)


def _gather_params(links: list[str]) -> list[migration.OtpParameters]:
    """Parse a (possibly mixed-scheme) list of links and flatten all accounts into one list."""
    params: list[migration.OtpParameters] = []
    for link in links:
        params.extend(_parse_any_link(link))
    return params


def _resolve_duplicates(params: list[migration.OtpParameters], *, prompt: bool = True) -> list[migration.OtpParameters]:
    """Resolve accounts sharing the same secret/issuer, keeping one survivor per group.

    When ``prompt`` is true, interactively asks which to keep; otherwise keeps
    the first account in each group without asking.
    """
    drop: set[int] = set()
    for group in migration.duplicate_groups(params):
        if not prompt:
            drop.update(group[1:])
            continue
        sample = params[group[0]]
        print(f"\nduplicate accounts (secret=***, issuer={sample.issuer!r}):", file=sys.stderr)
        for n, idx in enumerate(group, start=1):
            print(f"  [{n}] {sample.issuer}: {params[idx].name}", file=sys.stderr)
        print(f"keep which one? [1-{len(group)}, default 1]: ", end="", file=sys.stderr, flush=True)
        choice = input().strip()
        try:
            keep = int(choice) - 1 if choice else 0
        except ValueError:
            keep = -1
        if not 0 <= keep < len(group):
            print(f"invalid choice {choice!r}, keeping [1]", file=sys.stderr)
            keep = 0
        drop.update(idx for pos, idx in enumerate(group) if pos != keep)
    return [op for i, op in enumerate(params) if i not in drop]


def _rename_empty_issuers(params: list[migration.OtpParameters]) -> None:
    """Prompt for a replacement issuer and name for each account with no issuer."""
    for op in params:
        if op.issuer:
            continue
        print(f"\naccount with empty issuer (name={op.name!r}):", file=sys.stderr)
        print("new issuer: ", end="", file=sys.stderr, flush=True)
        op.issuer = input().strip()
        print(f"new name [{op.name}]: ", end="", file=sys.stderr, flush=True)
        new_name = input().strip()
        if new_name:
            op.name = new_name


def _apply_interactive_options(
    params: list[migration.OtpParameters],
    args: argparse.Namespace,
) -> list[migration.OtpParameters]:
    """Run ``--dedupe``/``--dedupe-with-default`` then ``--rename-empty-issuer``, in that order."""
    if args.dedupe:
        params = _resolve_duplicates(params)
    elif args.dedupe_with_default:
        params = _resolve_duplicates(params, prompt=False)
    if args.rename_empty_issuer:
        _rename_empty_issuers(params)
    return params


def _gather_links(args: argparse.Namespace) -> list[str]:
    """Collect links from positional args and any ``-f``/``--file`` files, in order."""
    links = list(args.links)
    for path in args.files:
        links.extend(_read_links(path))
    if not links:
        msg = "no links provided (pass LINK arguments and/or -f/--file)"
        raise ConversionError(msg)
    return links


_QR_HTML_TEMPLATE = """\
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>otpa QR codes</title>
<style>
  body {{
    font-family: system-ui, sans-serif;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 1rem;
    padding: 2rem;
  }}
  #counter {{ font-size: 1.1rem; }}
  #qr {{ width: 320px; height: 320px; image-rendering: pixelated; border: 1px solid #ccc; }}
  nav button {{ font-size: 1rem; padding: 0.4rem 1rem; margin: 0 0.5rem; cursor: pointer; }}
  nav button:disabled {{ opacity: 0.4; cursor: default; }}
</style>
</head>
<body>
  <a id="counter" target="_blank" rel="noopener"></a>
  <img id="qr" alt="QR code">
  <nav>
    <button id="prev" type="button">&lt;prev</button>
    <button id="next" type="button">next&gt;</button>
  </nav>
  <script>
    const images = {images_json};
    const links = {links_json};
    let i = 0;
    function render() {{
      document.getElementById("qr").src = "data:image/png;base64," + images[i];
      const counter = document.getElementById("counter");
      counter.textContent = "[" + (i + 1) + "/" + images.length + "]";
      counter.href = links[i];
      document.getElementById("prev").disabled = i === 0;
      document.getElementById("next").disabled = i === images.length - 1;
    }}
    document.getElementById("prev").addEventListener("click", () => {{
      i = Math.max(0, i - 1);
      render();
    }});
    document.getElementById("next").addEventListener("click", () => {{
      i = Math.min(images.length - 1, i + 1);
      render();
    }});
    render();
  </script>
</body>
</html>
"""


def _build_qr_html(links: list[str]) -> str:
    """Render ``links`` as base64 PNG QR codes embedded in a paginated HTML page."""
    images_b64 = []
    for link in links:
        buf = io.BytesIO()
        qrcode.make(link, image_factory=PyPNGImage).save(buf)
        images_b64.append(base64.b64encode(buf.getvalue()).decode("ascii"))
    return _QR_HTML_TEMPLATE.format(images_json=json.dumps(images_b64), links_json=json.dumps(links))


def _show_qr_codes(links: list[str]) -> None:
    """Write ``links`` as a paginated QR-code HTML page to a temp file and open it in a browser."""
    html = _build_qr_html(links)
    fd, path = tempfile.mkstemp(prefix="otpa-qr-", suffix=".html")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(html)
    webbrowser.open_new_tab(f"file://{path}")


def _emit_links(links: list[str], args: argparse.Namespace) -> None:
    """Print ``links`` to stdout, or show them as QR codes instead if ``--qr`` was passed."""
    if args.qr:
        _show_qr_codes(links)
    else:
        for link in links:
            print(link)


def cmd_cm(args: argparse.Namespace) -> int:
    """`otpa cm`: convert any mix of otpauth:// / otpauth-migration:// links into migration link(s)."""
    params = _gather_params(_gather_links(args))
    params = _apply_interactive_options(params, args)
    out_links = migration.params_to_migration(params, batch_size=args.batch_size)
    _emit_links(out_links, args)
    return 0


def cmd_ca(args: argparse.Namespace) -> int:
    """`otpa ca`: convert any mix of otpauth:// / otpauth-migration:// links into otpauth:// links."""
    params = _gather_params(_gather_links(args))
    params = _apply_interactive_options(params, args)
    out_links = [migration.params_to_otpauth_link(op) for op in params]
    _emit_links(out_links, args)
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    """`otpa i`: print details for either link type."""
    scheme = migration.detect_scheme(args.link)
    if scheme == migration.MIGRATION_SCHEME:
        _print_migration_info(args.link)
    elif scheme == migration.OTPAUTH_SCHEME:
        _print_otpauth_info(args.link)
    else:
        msg = f"unrecognized link scheme: {scheme!r}"
        raise ConversionError(msg)
    return 0


def _print_migration_info(link: str) -> None:
    payload = migration.migration_link_to_payload(link)
    print(f"type:        {migration.MIGRATION_SCHEME}")
    print(f"version:     {payload.version}")
    print(f"batch size:  {payload.batch_size}")
    print(f"batch index: {payload.batch_index}")
    print(f"batch id:    {payload.batch_id}")
    print(f"accounts:    {len(payload.otp_parameters)}")
    for i, op in enumerate(payload.otp_parameters):
        print(f"\n[{i}]")
        _print_params(op)


def _print_otpauth_info(link: str) -> None:
    op = migration.otpauth_link_to_params(link)
    print(f"type:        {migration.OTPAUTH_SCHEME}")
    _print_params(op)


def _print_params(op: migration.OtpParameters) -> None:
    print(f"  otp type:  {op.type.label}")
    print(f"  name:      {op.name}")
    print(f"  issuer:    {op.issuer}")
    print(f"  secret:    {migration.encode_secret(op.secret)}")
    print(f"  algorithm: {op.algorithm.label}")
    print(f"  digits:    {op.digits.count}")
    if op.type is migration.OtpType.HOTP:
        print(f"  counter:   {op.counter}")
    if op.unique_id:
        print(f"  unique id: {op.unique_id}")


if __name__ == "__main__":
    raise SystemExit(main())
