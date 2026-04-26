"""Tests for audit.py, outbox.py, and pipeline.py."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from doc_automation.audit import AuditLogger
from doc_automation.config import (
    AnomalyRule,
    AnomalyRulesConfig,
    COARow,
    Config,
    DefaultsConfig,
    PathsConfig,
)
from doc_automation.extraction.invoice import Invoice
from doc_automation.outbox import Outbox
from doc_automation.pipeline import Pipeline, PipelineResult


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_invoice(
    number: str = "INV-001",
    vendor: str = "ACME Inc.",
    total: str = "500.00",
    flags: list[str] | None = None,
) -> Invoice:
    inv = Invoice(source_file=Path("inv.pdf"), template_used="_default")
    inv.invoice_number = number
    inv.vendor_name = vendor
    inv.total = Decimal(total)
    inv.invoice_date = date(2024, 1, 15)
    inv.gl_code = "6100"
    inv.anomaly_flags = flags or []
    return inv


def make_config(tmp_path: Path) -> Config:
    return Config(
        paths=PathsConfig(
            working_dir=tmp_path / "working",
            quarantine_dir=tmp_path / "quarantine",
            audit_log=tmp_path / "logs" / "audit.jsonl",
        ),
        defaults=DefaultsConfig(amount_threshold=10_000.0),
    )


def make_rules(block_rule: str | None = None) -> AnomalyRulesConfig:
    rules = [
        AnomalyRule(
            name="duplicate_invoice",
            severity="block",
            description="Duplicate invoice number",
            params={},
        ),
        AnomalyRule(
            name="amount_threshold",
            severity="warn",
            description="Amount over threshold",
            params={"threshold": 10000.0},
        ),
    ]
    return AnomalyRulesConfig(rules=rules)


def make_coa() -> list[COARow]:
    return [
        COARow(
            gl_code="6100",
            name="Office Supplies",
            vendor_match="ACME",
            default_for_unmatched=False,
        ),
        COARow(
            gl_code="9999",
            name="Uncategorised",
            default_for_unmatched=True,
        ),
    ]


# ── AuditLogger ───────────────────────────────────────────────────────────────

class TestAuditLogger:
    def test_creates_log_file(self, tmp_path: Path) -> None:
        log_path = tmp_path / "logs" / "audit.jsonl"
        audit = AuditLogger(log_path)
        audit.log_invoice(make_invoice(), status="ok")
        assert log_path.exists()

    def test_log_invoice_writes_valid_json(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLogger(log_path)
        audit.log_invoice(make_invoice(number="INV-001", vendor="Corp A"), status="ok")
        line = log_path.read_text(encoding="utf-8").strip()
        entry = json.loads(line)
        assert entry["status"] == "ok"
        assert entry["invoice_number"] == "INV-001"
        assert entry["vendor_name"] == "Corp A"

    def test_log_invoice_blocked_includes_flags(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLogger(log_path)
        inv = make_invoice(flags=["duplicate_invoice"])
        audit.log_invoice(inv, status="blocked")
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["status"] == "blocked"
        assert "duplicate_invoice" in entry["anomaly_flags"]

    def test_log_invoice_includes_error_field(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLogger(log_path)
        audit.log_invoice(make_invoice(), status="output_error", error="connection refused")
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["error"] == "connection refused"

    def test_log_parse_error(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLogger(log_path)
        audit.log_parse_error(Path("bad.pdf"), "corrupted PDF")
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["status"] == "parse_error"
        assert entry["source_file"] == "bad.pdf"
        assert "corrupted" in entry["error"]

    def test_multiple_entries_appended(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLogger(log_path)
        audit.log_invoice(make_invoice(number="A"), status="ok")
        audit.log_invoice(make_invoice(number="B"), status="blocked")
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["invoice_number"] == "A"
        assert json.loads(lines[1])["invoice_number"] == "B"

    def test_entries_have_iso_timestamp(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLogger(log_path)
        audit.log_invoice(make_invoice(), status="ok")
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        datetime.fromisoformat(entry["ts"])  # should not raise


# ── Outbox ────────────────────────────────────────────────────────────────────

class TestOutbox:
    def test_put_and_drain(self, tmp_path: Path) -> None:
        outbox = Outbox(tmp_path / "outbox.sqlite")
        outbox.put(make_invoice(number="INV-X"), "network error")
        due = outbox.drain()
        assert len(due) == 1
        assert due[0][1].invoice_number == "INV-X"

    def test_mark_done_removes_entry(self, tmp_path: Path) -> None:
        outbox = Outbox(tmp_path / "outbox.sqlite")
        outbox.put(make_invoice(), "err")
        entry_id, _ = outbox.drain()[0]
        outbox.mark_done(entry_id)
        assert outbox.drain() == []

    def test_reschedule_pushes_next_retry(self, tmp_path: Path) -> None:
        outbox = Outbox(tmp_path / "outbox.sqlite")
        outbox.put(make_invoice(), "err")
        entry_id, _ = outbox.drain()[0]
        outbox.reschedule(entry_id, "still failing")
        # After reschedule, next_retry_at is in the future → drain returns nothing
        assert outbox.drain() == []

    def test_len_returns_count(self, tmp_path: Path) -> None:
        outbox = Outbox(tmp_path / "outbox.sqlite")
        assert len(outbox) == 0
        outbox.put(make_invoice(number="A"), "err")
        outbox.put(make_invoice(number="B"), "err")
        assert len(outbox) == 2

    def test_roundtrip_preserves_invoice_fields(self, tmp_path: Path) -> None:
        inv = make_invoice(number="RT-01", total="1234.56")
        inv.invoice_date = date(2024, 6, 1)
        inv.currency = "EUR"
        outbox = Outbox(tmp_path / "outbox.sqlite")
        outbox.put(inv, "err")
        _, restored = outbox.drain()[0]
        assert restored.invoice_number == "RT-01"
        assert restored.total == Decimal("1234.56")
        assert restored.invoice_date == date(2024, 6, 1)
        assert restored.currency == "EUR"

    def test_close_then_reopen(self, tmp_path: Path) -> None:
        db_path = tmp_path / "outbox.sqlite"
        outbox = Outbox(db_path)
        outbox.put(make_invoice(), "err")
        outbox.close()
        outbox2 = Outbox(db_path)
        assert len(outbox2) == 1
        outbox2.close()


# ── Pipeline ──────────────────────────────────────────────────────────────────

class TestPipeline:
    def _make_pipeline(
        self, tmp_path: Path, output_adapter=None, email_source=None,
        audit=None, outbox=None, templates_dir=None,
    ) -> Pipeline:
        if output_adapter is None:
            output_adapter = MagicMock()
            output_adapter.write_rows.return_value = 1
        return Pipeline(
            config=make_config(tmp_path),
            rules=make_rules(),
            coa=make_coa(),
            output_adapter=output_adapter,
            email_source=email_source,
            audit_logger=audit,
            outbox=outbox,
            templates_dir=templates_dir or (tmp_path / "templates"),
        )

    def test_run_no_email_source_returns_empty_result(self, tmp_path: Path) -> None:
        pipeline = self._make_pipeline(tmp_path)
        result = pipeline.run()
        assert result.processed == 0
        assert result.output_rows == 0

    def test_process_file_extracts_invoice(self, tmp_path: Path) -> None:
        """process_file should return an Invoice for a parseable file."""
        import fitz
        pdf_path = tmp_path / "invoice.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text(
            (50, 100),
            "ACME Supplies Inc.\nInvoice No: INV-2024-001\n"
            "Date: 2024-01-15\nTotal: $1,650.00",
        )
        doc.save(str(pdf_path))

        templates_dir = Path("config/templates")
        pipeline = self._make_pipeline(tmp_path, templates_dir=templates_dir)
        invoice = pipeline.process_file(pdf_path)
        assert invoice is not None
        assert isinstance(invoice, Invoice)

    def test_outbox_drained_before_email_fetch(self, tmp_path: Path) -> None:
        mock_output = MagicMock()
        mock_output.write_rows.return_value = 1
        outbox = Outbox(tmp_path / "outbox.sqlite")
        outbox.put(make_invoice(number="RETRY-01"), "prev error")

        pipeline = self._make_pipeline(tmp_path, output_adapter=mock_output, outbox=outbox)
        result = pipeline.run()
        assert result.outbox_retried == 1
        assert mock_output.write_rows.called

    def test_outbox_reschedules_on_output_failure(self, tmp_path: Path) -> None:
        mock_output = MagicMock()
        mock_output.write_rows.side_effect = IOError("output unreachable")
        outbox = Outbox(tmp_path / "outbox.sqlite")
        outbox.put(make_invoice(), "original error")

        pipeline = self._make_pipeline(tmp_path, output_adapter=mock_output, outbox=outbox)
        result = pipeline.run()
        assert result.outbox_still_pending == 1
        assert len(outbox) == 1  # still queued

    def test_audit_logged_on_ok(self, tmp_path: Path) -> None:
        audit_path = tmp_path / "audit.jsonl"
        audit = AuditLogger(audit_path)
        outbox = Outbox(tmp_path / "outbox.sqlite")
        outbox.put(make_invoice(number="AU-01"), "err")

        mock_output = MagicMock()
        mock_output.write_rows.return_value = 1
        pipeline = self._make_pipeline(
            tmp_path, output_adapter=mock_output, audit=audit, outbox=outbox
        )
        pipeline.run()

        entries = [
            json.loads(l)
            for l in audit_path.read_text(encoding="utf-8").strip().splitlines()
        ]
        assert any(e["status"] == "ok" for e in entries)

    def test_email_source_attachments_processed(self, tmp_path: Path) -> None:
        import fitz
        from doc_automation.email_ingest.base import EmailMessage

        pdf_path = tmp_path / "working" / "attach.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 100), "ACME Supplies Inc.\nTotal: $500.00")
        doc.save(str(pdf_path))

        mock_email = MagicMock()
        mock_email.fetch_new.return_value = [
            EmailMessage(
                uid="42",
                subject="Invoice",
                sender="billing@acme.com",
                received_at=datetime.now(tz=timezone.utc),
                attachments=[pdf_path],
            )
        ]
        mock_output = MagicMock()
        mock_output.write_rows.return_value = 1

        pipeline = self._make_pipeline(
            tmp_path,
            output_adapter=mock_output,
            email_source=mock_email,
            templates_dir=Path("config/templates"),
        )
        result = pipeline.run()
        assert result.processed >= 1
        mock_email.mark_processed.assert_called_with("42")

    def test_pipeline_result_str(self) -> None:
        r = PipelineResult(processed=5, blocked=1, output_rows=4)
        assert "processed=5" in str(r)
        assert "blocked=1" in str(r)
