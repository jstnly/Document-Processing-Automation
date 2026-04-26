"""Tests for the CLI (cli.py).

Each _cmd_* function is called directly with a mock argparse.Namespace so that
tests run without subprocess overhead and can isolate I/O via mocks.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Real config directory (Phase 1 committed files)
CONFIG_DIR = Path(__file__).parent.parent / "config"


def _ns(**kwargs: object) -> argparse.Namespace:
    """Build a minimal argparse.Namespace for a subcommand."""
    defaults = {"config_dir": str(CONFIG_DIR), "verbose": False}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ── _setup_logging ────────────────────────────────────────────────────────────


def test_setup_logging_calls_basicconfig_info() -> None:
    import logging
    from doc_automation.cli import _setup_logging
    with patch("doc_automation.cli.logging") as mock_logging:
        mock_logging.DEBUG = logging.DEBUG
        mock_logging.INFO = logging.INFO
        _setup_logging(verbose=False)
        mock_logging.basicConfig.assert_called_once()
        call_kwargs = mock_logging.basicConfig.call_args
        assert call_kwargs.kwargs.get("level") == logging.INFO


def test_setup_logging_calls_basicconfig_debug() -> None:
    import logging
    from doc_automation.cli import _setup_logging
    with patch("doc_automation.cli.logging") as mock_logging:
        mock_logging.DEBUG = logging.DEBUG
        mock_logging.INFO = logging.INFO
        _setup_logging(verbose=True)
        call_kwargs = mock_logging.basicConfig.call_args
        assert call_kwargs.kwargs.get("level") == logging.DEBUG


# ── validate-config ───────────────────────────────────────────────────────────


def test_validate_config_ok(capsys: pytest.CaptureFixture) -> None:
    from doc_automation.cli import _cmd_validate_config
    rc = _cmd_validate_config(_ns())
    assert rc == 0
    captured = capsys.readouterr()
    assert "Config OK" in captured.out


def test_validate_config_bad_dir(capsys: pytest.CaptureFixture) -> None:
    from doc_automation.cli import _cmd_validate_config
    rc = _cmd_validate_config(_ns(config_dir="/nonexistent/config"))
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.err  # error written to stderr


# ── list-templates ────────────────────────────────────────────────────────────


def test_list_templates_with_real_templates(capsys: pytest.CaptureFixture) -> None:
    from doc_automation.cli import _cmd_list_templates
    rc = _cmd_list_templates(_ns())
    assert rc == 0
    captured = capsys.readouterr()
    assert "_default" in captured.out
    assert "(default fallback)" in captured.out


def test_list_templates_empty_dir(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    from doc_automation.cli import _cmd_list_templates
    (tmp_path / "templates").mkdir()
    rc = _cmd_list_templates(_ns(config_dir=str(tmp_path)))
    assert rc == 0
    captured = capsys.readouterr()
    assert "No templates found" in captured.out


# ── process-file ──────────────────────────────────────────────────────────────


def test_process_file_not_found(capsys: pytest.CaptureFixture) -> None:
    from doc_automation.cli import _cmd_process_file
    rc = _cmd_process_file(_ns(path="/no/such/file.pdf"))
    assert rc == 1
    assert "File not found" in capsys.readouterr().err


def test_process_file_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    from doc_automation.cli import _cmd_process_file
    pdf = tmp_path / "inv.pdf"
    pdf.write_bytes(b"dummy")
    rc = _cmd_process_file(_ns(config_dir=str(tmp_path), path=str(pdf)))
    assert rc == 1
    assert capsys.readouterr().err  # ConfigError printed to stderr


def test_process_file_success(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Full process-file path with a real PDF and mocked output adapter."""
    import fitz
    from doc_automation.cli import _cmd_process_file

    pdf_path = tmp_path / "inv.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (50, 100),
        "ACME Supplies Inc.\nInvoice No: CLI-001\nDate: 2024-01-15\nTotal: $500.00",
    )
    doc.save(str(pdf_path))

    mock_adapter = MagicMock()
    mock_adapter.write_rows.return_value = 1

    with patch("doc_automation.output.build_adapter", return_value=mock_adapter):
        rc = _cmd_process_file(_ns(path=str(pdf_path)))

    assert rc == 0
    out = capsys.readouterr().out
    assert "template:" in out
    mock_adapter.write_rows.assert_called_once()
    mock_adapter.close.assert_called_once()


def test_process_file_output_write_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """process-file returns 1 when write_rows raises."""
    import fitz
    from doc_automation.cli import _cmd_process_file

    pdf_path = tmp_path / "inv.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 100), "ACME Supplies Inc.\nTotal: $500.00")
    doc.save(str(pdf_path))

    mock_adapter = MagicMock()
    mock_adapter.write_rows.side_effect = OSError("sheets down")

    with patch("doc_automation.output.build_adapter", return_value=mock_adapter):
        rc = _cmd_process_file(_ns(path=str(pdf_path)))

    assert rc == 1
    assert "Output write failed" in capsys.readouterr().err


# ── run ───────────────────────────────────────────────────────────────────────


def test_run_config_error(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    from doc_automation.cli import _cmd_run
    rc = _cmd_run(_ns(config_dir=str(tmp_path)))
    assert rc == 1
    assert capsys.readouterr().err


def test_run_success(capsys: pytest.CaptureFixture) -> None:
    from doc_automation.cli import _cmd_run
    from doc_automation.pipeline import PipelineResult

    mock_adapter = MagicMock()
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = PipelineResult(processed=2, output_rows=2)

    with (
        patch("doc_automation.output.build_adapter", return_value=mock_adapter),
        patch("doc_automation.pipeline.Pipeline", return_value=mock_pipeline),
    ):
        rc = _cmd_run(_ns())

    assert rc == 0
    assert "Run complete" in capsys.readouterr().out
    mock_pipeline.run.assert_called_once()


def test_run_closes_resources_on_success(capsys: pytest.CaptureFixture) -> None:
    """Adapter and email source are closed after a successful run."""
    from doc_automation.cli import _cmd_run
    from doc_automation.pipeline import PipelineResult

    mock_adapter = MagicMock()
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = PipelineResult()

    with (
        patch("doc_automation.output.build_adapter", return_value=mock_adapter),
        patch("doc_automation.pipeline.Pipeline", return_value=mock_pipeline),
    ):
        _cmd_run(_ns())

    mock_adapter.close.assert_called_once()


def test_run_with_mailbox_builds_and_closes_email_source(
    capsys: pytest.CaptureFixture,
) -> None:
    """cli.py:69, 92 — email source built from mailbox config and closed after run."""
    from doc_automation.cli import _cmd_run
    from doc_automation.pipeline import PipelineResult

    mock_adapter = MagicMock()
    mock_email = MagicMock()
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = PipelineResult()

    # Build a config with mailbox before patching
    from doc_automation.config import MailboxConfig, load_all_configs as _real_load
    real_config, real_rules, real_coa = _real_load(CONFIG_DIR)
    config_with_mailbox = real_config.model_copy(update={"mailbox": MailboxConfig(
        host="imap.example.com",
        username_env="IMAP_USER",
        password_env="IMAP_PASS",
    )})

    with (
        patch("doc_automation.output.build_adapter", return_value=mock_adapter),
        patch("doc_automation.pipeline.Pipeline", return_value=mock_pipeline),
        patch(
            "doc_automation.config.load_all_configs",
            return_value=(config_with_mailbox, real_rules, real_coa),
        ),
        patch("doc_automation.email_ingest.build_email_source", return_value=mock_email),
    ):
        rc = _cmd_run(_ns())

    assert rc == 0
    mock_email.close.assert_called_once()


# ── replay-quarantine ─────────────────────────────────────────────────────────


def test_replay_quarantine_empty_dir(capsys: pytest.CaptureFixture) -> None:
    from doc_automation.cli import _cmd_replay_quarantine
    rc = _cmd_replay_quarantine(_ns())
    assert rc == 0
    assert "No files in quarantine" in capsys.readouterr().out


def test_replay_quarantine_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    from doc_automation.cli import _cmd_replay_quarantine
    rc = _cmd_replay_quarantine(_ns(config_dir=str(tmp_path)))
    assert rc == 1
    assert capsys.readouterr().err


def test_replay_quarantine_processes_pdfs(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """replay-quarantine replays files found in the configured quarantine dir."""
    import fitz
    from doc_automation.cli import _cmd_replay_quarantine

    # Create a quarantine PDF
    quarantine_dir = tmp_path / "quarantine"
    quarantine_dir.mkdir()
    pdf_path = quarantine_dir / "blocked.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 100), "ACME Supplies Inc.\nTotal: $500.00")
    doc.save(str(pdf_path))

    mock_adapter = MagicMock()
    mock_adapter.write_rows.return_value = 1
    mock_pipeline = MagicMock()
    mock_pipeline.process_file.return_value = MagicMock(anomaly_flags=[])

    with (
        patch("doc_automation.output.build_adapter", return_value=mock_adapter),
        patch("doc_automation.pipeline.Pipeline", return_value=mock_pipeline),
        patch(
            "doc_automation.config.load_all_configs",
            return_value=_load_configs_with_quarantine(tmp_path),
        ),
    ):
        rc = _cmd_replay_quarantine(_ns())

    assert rc == 0
    out = capsys.readouterr().out
    assert "Replayed" in out


def test_replay_quarantine_logs_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """cli.py:227-228 — process_file raising is caught and printed to stderr."""
    from doc_automation.cli import _cmd_replay_quarantine

    quarantine_dir = tmp_path / "quarantine"
    quarantine_dir.mkdir()
    (quarantine_dir / "bad.pdf").write_bytes(b"junk")

    mock_adapter = MagicMock()
    mock_pipeline = MagicMock()
    mock_pipeline.process_file.side_effect = ValueError("parse failed")

    with (
        patch("doc_automation.output.build_adapter", return_value=mock_adapter),
        patch("doc_automation.pipeline.Pipeline", return_value=mock_pipeline),
        patch(
            "doc_automation.config.load_all_configs",
            return_value=_load_configs_with_quarantine(tmp_path),
        ),
    ):
        rc = _cmd_replay_quarantine(_ns())

    assert rc == 0  # returns 0 even on per-file failure
    captured = capsys.readouterr()
    assert "failed" in captured.err
    assert "Replayed 0/1" in captured.out


def _load_configs_with_quarantine(tmp_path: Path):
    """Return real configs with quarantine_dir patched to tmp_path/quarantine."""
    from doc_automation.config import load_all_configs, PathsConfig

    config, rules, coa = load_all_configs(CONFIG_DIR)
    config = config.model_copy(
        update={"paths": PathsConfig(
            working_dir=config.paths.working_dir,
            quarantine_dir=tmp_path / "quarantine",
            audit_log=tmp_path / "audit.jsonl",
        )}
    )
    return config, rules, coa


# ── _build_parser ─────────────────────────────────────────────────────────────


def test_build_parser_subcommands() -> None:
    from doc_automation.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["validate-config"])
    assert args.command == "validate-config"


def test_build_parser_process_file_path() -> None:
    from doc_automation.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["process-file", "my_invoice.pdf"])
    assert args.command == "process-file"
    assert args.path == "my_invoice.pdf"


def test_build_parser_verbose_flag() -> None:
    from doc_automation.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["-v", "validate-config"])
    assert args.verbose is True


def test_main_calls_sys_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() calls sys.exit with the handler's return code."""
    from doc_automation.cli import main

    monkeypatch.setattr(sys, "argv", ["doc-automation", "validate-config"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
