"""Tests for otpa.cli."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pytest

from otpa import cli, migration

MIGRATION_LINK = "otpauth-migration://offline?data=CjEKCkhlbGxvId6tvu8SGEV4YW1wbGU6YWxpY2VAZ29vZ2xlLmNvbRoHRXhhbXBsZTAC"
OTPAUTH_LINK = "otpauth://totp/Example:alice@google.com?secret=JBSWY3DPEHPK3PXP&issuer=Example&period=30"


def _otpauth_link(i):
    return f"otpauth://totp/Svc:user{i}?secret=JBSWY3DPEHPK3PXP&issuer=Svc"


def test_ca_decodes_migration_link(capsys):
    assert cli.main(["ca", MIGRATION_LINK]) == 0
    out = capsys.readouterr().out.strip()
    assert out == OTPAUTH_LINK


def test_ca_passes_through_otpauth_link(capsys):
    assert cli.main(["ca", OTPAUTH_LINK]) == 0
    assert capsys.readouterr().out.strip() == OTPAUTH_LINK


def test_cm_encodes_otpauth_link_round_trip(capsys):
    assert cli.main(["cm", OTPAUTH_LINK]) == 0
    link = capsys.readouterr().out.strip()
    assert link.startswith("otpauth-migration://offline?data=")
    assert cli.main(["ca", link]) == 0
    assert capsys.readouterr().out.strip() == OTPAUTH_LINK


def test_cm_reencodes_migration_link(capsys):
    assert cli.main(["cm", MIGRATION_LINK]) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("otpauth-migration://offline?data=")
    payload = migration.migration_link_to_payload(out)
    assert len(payload.otp_parameters) == 1


def test_cm_mixes_otpauth_and_migration_inputs(capsys):
    assert cli.main(["cm", MIGRATION_LINK, _otpauth_link(0)]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    payload = migration.migration_link_to_payload(out[0])
    assert len(payload.otp_parameters) == 2


def test_ca_mixes_otpauth_and_migration_inputs(capsys):
    assert cli.main(["ca", MIGRATION_LINK, _otpauth_link(0)]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2
    assert out[0] == OTPAUTH_LINK
    assert out[1] == _otpauth_link(0) + "&period=30"


def test_ca_reads_migration_links_from_file(tmp_path, capsys):
    f = tmp_path / "links.txt"
    f.write_text(f"# a comment\n\n{MIGRATION_LINK}\n", encoding="utf-8")
    assert cli.main(["ca", "-f", str(f)]) == 0
    assert capsys.readouterr().out.strip() == OTPAUTH_LINK


def test_cm_reads_otpauth_links_from_file(tmp_path, capsys):
    f = tmp_path / "links.txt"
    f.write_text(f"{OTPAUTH_LINK}\n", encoding="utf-8")
    assert cli.main(["cm", "-f", str(f)]) == 0
    assert capsys.readouterr().out.strip().startswith("otpauth-migration://offline?data=")


def test_cm_combines_positional_links_and_files(tmp_path, capsys):
    f = tmp_path / "links.txt"
    f.write_text(f"{_otpauth_link(0)}\n", encoding="utf-8")
    assert cli.main(["cm", _otpauth_link(1), "-f", str(f)]) == 0
    out_lines = capsys.readouterr().out.strip().splitlines()
    assert len(out_lines) == 1
    payload = migration.migration_link_to_payload(out_lines[0])
    assert len(payload.otp_parameters) == 2


def test_cm_accepts_multiple_file_flags(tmp_path, capsys):
    f1 = tmp_path / "links1.txt"
    f2 = tmp_path / "links2.txt"
    f1.write_text(f"{_otpauth_link(0)}\n", encoding="utf-8")
    f2.write_text(f"{_otpauth_link(1)}\n", encoding="utf-8")
    assert cli.main(["cm", "-f", str(f1), "-f", str(f2)]) == 0
    out_lines = capsys.readouterr().out.strip().splitlines()
    assert len(out_lines) == 1
    payload = migration.migration_link_to_payload(out_lines[0])
    assert len(payload.otp_parameters) == 2


def test_cm_splits_into_multiple_links_by_default_batch_size(capsys):
    links = [_otpauth_link(i) for i in range(12)]
    assert cli.main(["cm", *links]) == 0
    out_lines = capsys.readouterr().out.strip().splitlines()
    assert len(out_lines) == 2
    for line in out_lines:
        assert line.startswith("otpauth-migration://offline?data=")


def test_cm_respects_custom_batch_size(capsys):
    links = [_otpauth_link(i) for i in range(5)]
    assert cli.main(["cm", "--batch-size", "2", *links]) == 0
    out_lines = capsys.readouterr().out.strip().splitlines()
    assert len(out_lines) == 3


def test_cm_respects_batch_size_short_flag(capsys):
    links = [_otpauth_link(i) for i in range(5)]
    assert cli.main(["cm", "-n", "2", *links]) == 0
    out_lines = capsys.readouterr().out.strip().splitlines()
    assert len(out_lines) == 3


def test_cm_batch_size_applies_after_flattening_mixed_inputs(capsys):
    packed = migration.otpauth_to_migration([_otpauth_link(0), _otpauth_link(1)])[0]
    assert cli.main(["cm", "-n", "2", packed, _otpauth_link(2)]) == 0
    out_lines = capsys.readouterr().out.strip().splitlines()
    assert len(out_lines) == 2


def test_cm_invalid_batch_size_errors(capsys):
    assert cli.main(["cm", "--batch-size", "0", OTPAUTH_LINK]) == 1
    assert "error:" in capsys.readouterr().err


def test_ca_rejects_batch_size_option(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["ca", "-n", "2", OTPAUTH_LINK])
    assert exc_info.value.code == 2


@pytest.mark.parametrize("command", ["cm", "ca"])
def test_batch_id_option_is_removed(command):
    with pytest.raises(SystemExit) as exc_info:
        cli.main([command, "--batch-id", "1", OTPAUTH_LINK])
    assert exc_info.value.code == 2


def test_cm_unrecognized_scheme_errors(capsys):
    assert cli.main(["cm", "https://example.com"]) == 1
    assert "error:" in capsys.readouterr().err


def test_ca_unrecognized_scheme_errors(capsys):
    assert cli.main(["ca", "https://example.com"]) == 1
    assert "error:" in capsys.readouterr().err


def test_cm_no_links_errors(capsys):
    assert cli.main(["cm"]) == 1
    assert "error:" in capsys.readouterr().err


def test_ca_no_links_errors(capsys):
    assert cli.main(["ca"]) == 1
    assert "error:" in capsys.readouterr().err


def test_info_migration(capsys):
    assert cli.main(["i", MIGRATION_LINK]) == 0
    out = capsys.readouterr().out
    assert "otpauth-migration" in out
    assert "accounts:    1" in out
    assert "JBSWY3DPEHPK3PXP" in out


def test_info_otpauth(capsys):
    assert cli.main(["i", OTPAUTH_LINK]) == 0
    out = capsys.readouterr().out
    assert "issuer:    Example" in out
    assert "JBSWY3DPEHPK3PXP" in out


def test_info_unknown_scheme_errors(capsys):
    assert cli.main(["i", "https://example.com"]) == 1
    assert "error:" in capsys.readouterr().err


def test_cm_bad_link_errors(capsys):
    assert cli.main(["cm", "otpauth://totp/x?issuer=y"]) == 1
    assert "error:" in capsys.readouterr().err


def test_ca_bad_link_errors(capsys):
    assert cli.main(["ca", "otpauth://totp/x?issuer=y"]) == 1
    assert "error:" in capsys.readouterr().err


def test_empty_file_errors(tmp_path, capsys):
    f = tmp_path / "empty.txt"
    f.write_text("# only a comment\n", encoding="utf-8")
    assert cli.main(["cm", "-f", str(f)]) == 1
    assert "error:" in capsys.readouterr().err


def test_missing_file_errors(capsys):
    assert cli.main(["cm", "-f", "/no/such/file.txt"]) == 1
    assert "error:" in capsys.readouterr().err


def test_no_command_exits(capsys):
    with pytest.raises(SystemExit):
        cli.main([])


def _dup_otpauth_link(name):
    return f"otpauth://totp/Svc:{name}?secret=JBSWY3DPEHPK3PXP&issuer=Svc"


def test_ca_dedupe_prompts_and_keeps_choice(monkeypatch, capsys):
    links = migration.otpauth_to_migration([_dup_otpauth_link("a"), _dup_otpauth_link("b")])
    monkeypatch.setattr("builtins.input", lambda: "2")
    assert cli.main(["ca", "--dedupe", *links]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == [_dup_otpauth_link("b") + "&period=30"]


def test_ca_dedupe_defaults_to_first_on_blank_input(monkeypatch, capsys):
    links = migration.otpauth_to_migration([_dup_otpauth_link("a"), _dup_otpauth_link("b")])
    monkeypatch.setattr("builtins.input", lambda: "")
    assert cli.main(["ca", "-d", *links]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == [_dup_otpauth_link("a") + "&period=30"]


def test_ca_dedupe_prompt_prefixes_names_with_issuer(monkeypatch, capsys):
    links = migration.otpauth_to_migration([_dup_otpauth_link("a"), _dup_otpauth_link("b")])
    monkeypatch.setattr("builtins.input", lambda: "1")
    assert cli.main(["ca", "--dedupe", *links]) == 0
    err = capsys.readouterr().err
    assert "[1] Svc: Svc:a" in err
    assert "[2] Svc: Svc:b" in err


def test_ca_dedupe_no_prompt_without_duplicates(monkeypatch, capsys):
    def fail_input():
        msg = "input() should not be called"
        raise AssertionError(msg)

    monkeypatch.setattr("builtins.input", fail_input)
    links = migration.otpauth_to_migration(["otpauth://totp/A:a?secret=JBSWY3DPEHPK3PXP&issuer=A"])
    assert cli.main(["ca", "--dedupe", *links]) == 0
    out = capsys.readouterr().out.strip()
    assert out == "otpauth://totp/A:a?secret=JBSWY3DPEHPK3PXP&issuer=A&period=30"


def test_ca_dedupe_reads_from_file(monkeypatch, tmp_path, capsys):
    links = migration.otpauth_to_migration([_dup_otpauth_link("a"), _dup_otpauth_link("b")])
    f = tmp_path / "links.txt"
    f.write_text("\n".join(links) + "\n", encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda: "1")
    assert cli.main(["ca", "--dedupe", "-f", str(f)]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == [_dup_otpauth_link("a") + "&period=30"]


def test_cm_dedupe_also_applies_when_encoding_otpauth_links(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda: "2")
    assert cli.main(["cm", "--dedupe", _dup_otpauth_link("a"), _dup_otpauth_link("b")]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    payload = migration.migration_link_to_payload(out[0])
    assert len(payload.otp_parameters) == 1
    assert payload.otp_parameters[0].name == "Svc:b"


def test_ca_without_dedupe_prints_all_duplicates(capsys):
    links = migration.otpauth_to_migration([_dup_otpauth_link("a"), _dup_otpauth_link("b")])
    assert cli.main(["ca", *links]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2


def test_ca_dedupe_with_default_keeps_first_without_prompting(monkeypatch, capsys):
    def fail_input():
        msg = "input() should not be called"
        raise AssertionError(msg)

    monkeypatch.setattr("builtins.input", fail_input)
    links = migration.otpauth_to_migration([_dup_otpauth_link("a"), _dup_otpauth_link("b")])
    assert cli.main(["ca", "--dedupe-with-default", *links]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == [_dup_otpauth_link("a") + "&period=30"]


def test_ca_dedupe_with_default_short_flag(monkeypatch, capsys):
    def fail_input():
        msg = "input() should not be called"
        raise AssertionError(msg)

    monkeypatch.setattr("builtins.input", fail_input)
    links = migration.otpauth_to_migration([_dup_otpauth_link("a"), _dup_otpauth_link("b")])
    assert cli.main(["ca", "-D", *links]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == [_dup_otpauth_link("a") + "&period=30"]


def test_cm_dedupe_and_dedupe_with_default_are_mutually_exclusive(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["cm", "-d", "-D", OTPAUTH_LINK])
    assert exc_info.value.code == 2
    assert "not allowed" in capsys.readouterr().err


_EMPTY_ISSUER_LINK = "otpauth://totp/alice?secret=JBSWY3DPEHPK3PXP"


def test_cm_rename_empty_issuer_prompts_and_sets_values(monkeypatch, capsys):
    responses = iter(["NewIssuer", "renamed"])
    monkeypatch.setattr("builtins.input", lambda: next(responses))
    assert cli.main(["cm", "--rename-empty-issuer", _EMPTY_ISSUER_LINK]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    payload = migration.migration_link_to_payload(out[0])
    op = payload.otp_parameters[0]
    assert op.issuer == "NewIssuer"
    assert op.name == "renamed"


def test_cm_rename_empty_issuer_keeps_name_on_blank_input(monkeypatch, capsys):
    responses = iter(["NewIssuer", ""])
    monkeypatch.setattr("builtins.input", lambda: next(responses))
    assert cli.main(["cm", "-r", _EMPTY_ISSUER_LINK]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    payload = migration.migration_link_to_payload(out[0])
    op = payload.otp_parameters[0]
    assert op.issuer == "NewIssuer"
    assert op.name == "alice"


def test_cm_rename_empty_issuer_no_prompt_when_issuer_present(monkeypatch, capsys):
    def fail_input():
        msg = "input() should not be called"
        raise AssertionError(msg)

    monkeypatch.setattr("builtins.input", fail_input)
    assert cli.main(["cm", "--rename-empty-issuer", OTPAUTH_LINK]) == 0
    assert capsys.readouterr().out.strip().startswith("otpauth-migration://offline?data=")


def test_cm_dedupe_runs_before_rename_empty_issuer(monkeypatch, capsys):
    link_a = "otpauth://totp/alice?secret=JBSWY3DPEHPK3PXP"
    link_b = "otpauth://totp/alice2?secret=JBSWY3DPEHPK3PXP"
    responses = iter(["2", "NewIssuer", "renamed"])
    monkeypatch.setattr("builtins.input", lambda: next(responses))
    assert cli.main(["cm", "-d", "-r", link_a, link_b]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    payload = migration.migration_link_to_payload(out[0])
    assert len(payload.otp_parameters) == 1
    op = payload.otp_parameters[0]
    assert op.issuer == "NewIssuer"
    assert op.name == "renamed"


def test_ca_out_writes_to_file(tmp_path, capsys):
    out = tmp_path / "result.txt"
    assert cli.main(["ca", "-o", str(out), MIGRATION_LINK]) == 0
    assert out.read_text(encoding="utf-8").strip() == OTPAUTH_LINK
    assert capsys.readouterr().out == ""


def test_ca_out_errors_if_file_exists(tmp_path, capsys):
    out = tmp_path / "result.txt"
    out.write_text("existing\n", encoding="utf-8")
    assert cli.main(["ca", "-o", str(out), MIGRATION_LINK]) == 1
    assert "error:" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == "existing\n"


def test_ca_out_errors_if_path_is_directory(tmp_path, capsys):
    assert cli.main(["ca", "-o", str(tmp_path), MIGRATION_LINK]) == 1
    assert "error:" in capsys.readouterr().err


def test_ca_out_errors_if_parent_dir_missing(tmp_path, capsys):
    out = tmp_path / "missing" / "result.txt"
    assert cli.main(["ca", "-o", str(out), MIGRATION_LINK]) == 1
    assert "error:" in capsys.readouterr().err


def test_ca_overwrite_replaces_existing_file(tmp_path, capsys):
    out = tmp_path / "result.txt"
    out.write_text("existing\n", encoding="utf-8")
    assert cli.main(["ca", "-O", str(out), MIGRATION_LINK]) == 0
    assert out.read_text(encoding="utf-8").strip() == OTPAUTH_LINK
    assert capsys.readouterr().out == ""


def test_ca_overwrite_creates_new_file(tmp_path, capsys):
    out = tmp_path / "result.txt"
    assert cli.main(["ca", "--overwrite", str(out), MIGRATION_LINK]) == 0
    assert out.read_text(encoding="utf-8").strip() == OTPAUTH_LINK


def test_ca_out_and_overwrite_are_mutually_exclusive(tmp_path, capsys):
    out = tmp_path / "result.txt"
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["ca", "-o", str(out), "-O", str(out), MIGRATION_LINK])
    assert exc_info.value.code == 2
    assert "not allowed" in capsys.readouterr().err


def test_i_out_writes_to_file(tmp_path, capsys):
    out = tmp_path / "info.txt"
    assert cli.main(["i", "-o", str(out), OTPAUTH_LINK]) == 0
    text = out.read_text(encoding="utf-8")
    assert "issuer:    Example" in text
    assert capsys.readouterr().out == ""


def test_out_prompts_still_go_to_real_stderr_not_file(monkeypatch, tmp_path, capsys):
    links = migration.otpauth_to_migration([_dup_otpauth_link("a"), _dup_otpauth_link("b")])
    out = tmp_path / "result.txt"
    monkeypatch.setattr("builtins.input", lambda: "1")
    assert cli.main(["ca", "-d", "-o", str(out), *links]) == 0
    err = capsys.readouterr().err
    assert "duplicate accounts" in err
    assert "keep which one?" in err
    assert out.read_text(encoding="utf-8").strip() == _dup_otpauth_link("a") + "&period=30"


def _extract_qr_images(html: str) -> list[str]:
    match = re.search(r"const images = (\[.*?\]);", html, re.DOTALL)
    assert match is not None
    return json.loads(match.group(1))


def _extract_qr_links(html: str) -> list[str]:
    match = re.search(r"const links = (\[.*?\]);", html, re.DOTALL)
    assert match is not None
    return json.loads(match.group(1))


def test_cm_qr_opens_browser_with_html_page(monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open_new_tab", opened.append)
    assert cli.main(["cm", "--qr", OTPAUTH_LINK]) == 0
    assert capsys.readouterr().out == ""
    assert len(opened) == 1
    assert opened[0].startswith("file://")
    assert opened[0].endswith(".html")
    path = Path(opened[0].removeprefix("file://"))
    html = path.read_text(encoding="utf-8")
    path.unlink()
    assert "&lt;prev" in html
    assert "next&gt;" in html
    images = _extract_qr_images(html)
    assert len(images) == 1
    png_bytes = base64.b64decode(images[0])
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


def test_cm_qr_counter_links_to_the_underlying_migration_link(monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open_new_tab", opened.append)
    assert cli.main(["cm", "--qr", OTPAUTH_LINK]) == 0
    assert capsys.readouterr().out == ""
    path = Path(opened[0].removeprefix("file://"))
    html = path.read_text(encoding="utf-8")
    path.unlink()
    assert '<a id="counter"' in html
    expected = migration.otpauth_to_migration([OTPAUTH_LINK])
    assert _extract_qr_links(html) == expected


def test_cm_qr_embeds_one_image_and_link_per_output_link(monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open_new_tab", opened.append)
    links = [_otpauth_link(i) for i in range(5)]
    assert cli.main(["cm", "--qr", "-n", "2", *links]) == 0
    assert capsys.readouterr().out == ""
    html = Path(opened[0].removeprefix("file://")).read_text(encoding="utf-8")
    Path(opened[0].removeprefix("file://")).unlink()
    expected = migration.otpauth_to_migration(links, batch_size=2)
    images = _extract_qr_images(html)
    assert len(images) == len(expected) == 3
    assert _extract_qr_links(html) == expected


def test_cm_without_qr_does_not_open_browser(monkeypatch, capsys):
    def fail_open(_url):
        msg = "webbrowser.open_new_tab should not be called"
        raise AssertionError(msg)

    monkeypatch.setattr(cli.webbrowser, "open_new_tab", fail_open)
    assert cli.main(["cm", OTPAUTH_LINK]) == 0
    capsys.readouterr()


def test_ca_qr_opens_browser_instead_of_printing(monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open_new_tab", opened.append)
    assert cli.main(["ca", "--qr", MIGRATION_LINK]) == 0
    assert capsys.readouterr().out == ""
    assert len(opened) == 1
    path = Path(opened[0].removeprefix("file://"))
    html = path.read_text(encoding="utf-8")
    path.unlink()
    images = _extract_qr_images(html)
    assert len(images) == 1
    assert _extract_qr_links(html) == [OTPAUTH_LINK]


def test_ca_without_qr_still_prints(capsys):
    assert cli.main(["ca", MIGRATION_LINK]) == 0
    assert capsys.readouterr().out.strip() == OTPAUTH_LINK


def test_cm_qr_with_out_writes_nothing_to_file(monkeypatch, tmp_path, capsys):
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open_new_tab", opened.append)
    out = tmp_path / "result.txt"
    assert cli.main(["cm", "--qr", "-o", str(out), OTPAUTH_LINK]) == 0
    assert capsys.readouterr().out == ""
    assert out.read_text(encoding="utf-8") == ""
    assert len(opened) == 1
    Path(opened[0].removeprefix("file://")).unlink()
