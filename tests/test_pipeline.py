"""Tests for audit.py, outbox.py, and pipeline.py."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

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

    def test_roundtrip_with_line_items(self, tmp_path: Path) -> None:
        """outbox.py:53-55 — line items with numeric quantity/unit_price/amount serialised."""
        from decimal import Decimal as D

        from doc_automation.extraction.invoice import LineItem

        inv = make_invoice(number="LI-01")
        inv.line_items = [
            LineItem(
                description="Widget", quantity=D("2"), unit_price=D("10.00"), amount=D("20.00")
            ),
            LineItem(description="Tax", quantity=None, unit_price=None, amount=None),
        ]
        outbox = Outbox(tmp_path / "outbox.sqlite")
        outbox.put(inv, "err")
        _, restored = outbox.drain()[0]
        assert len(restored.line_items) == 2
        assert restored.line_items[0].quantity == D("2")
        assert restored.line_items[0].amount == D("20.00")
        assert restored.line_items[1].quantity is None

    def test_drain_skips_corrupted_entry(self, tmp_path: Path) -> None:
        """outbox.py:114-115 — corrupted JSON in outbox is logged and skipped."""
        from datetime import UTC, datetime

        db_path = tmp_path / "outbox.sqlite"
        outbox = Outbox(db_path)
        now = datetime.now(tz=UTC).isoformat()
        outbox._conn.execute(
            "INSERT INTO outbox (created_at, next_retry_at, invoice_json, last_error) "
            "VALUES (?, ?, ?, ?)",
            (now, now, "{{invalid json}}", "original error"),
        )
        outbox._conn.commit()
        result = outbox.drain()
        assert result == []  # corrupted entry skipped, no exception

    def test_reschedule_nonexistent_entry_is_noop(self, tmp_path: Path) -> None:
        """outbox.py:128 — reschedule with unknown entry_id is a no-op."""
        outbox = Outbox(tmp_path / "outbox.sqlite")
        outbox.reschedule(9999, "irrelevant")  # must not raise

    def test_del_swallows_exception_on_double_close(self, tmp_path: Path) -> None:
        """outbox.py:145-146 — __del__ except branch: swallows error if conn already closed."""
        from unittest.mock import patch

        outbox = Outbox(tmp_path / "outbox.sqlite")
        with patch.object(outbox._conn, "close", side_effect=Exception("already closed")):
            outbox.__del__()  # must not raise


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
        mock_output.write_rows.side_effect = OSError("output unreachable")
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
            json.loads(ln)
            for ln in audit_path.read_text(encoding="utf-8").strip().splitlines()
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
                received_at=datetime.now(tz=UTC),
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

    # ── _drain_outbox paths ───────────────────────────────────────────────────

    def test_drain_outbox_logs_audit_on_success(self, tmp_path: Path) -> None:
        """Line 109: audit logged when outbox drain succeeds."""
        audit_path = tmp_path / "audit.jsonl"
        audit = AuditLogger(audit_path)
        outbox = Outbox(tmp_path / "outbox.sqlite")
        outbox.put(make_invoice(number="DRAIN-AU"), "original")

        mock_output = MagicMock()
        mock_output.write_rows.return_value = 1
        pipeline = self._make_pipeline(
            tmp_path, output_adapter=mock_output, audit=audit, outbox=outbox
        )
        result = pipeline.run()

        assert result.outbox_retried == 1
        entries = [json.loads(ln) for ln in audit_path.read_text().strip().splitlines()]
        assert any(e["status"] == "ok" for e in entries)

    # ── _ingest_emails paths ──────────────────────────────────────────────────

    def test_ingest_email_fetch_error_increments_errors(self, tmp_path: Path) -> None:
        """Lines 122-125: fetch_new raises → result.errors incremented, pipeline continues."""
        mock_email = MagicMock()
        mock_email.fetch_new.side_effect = OSError("IMAP gone")
        pipeline = self._make_pipeline(tmp_path, email_source=mock_email)
        result = pipeline.run()
        assert result.errors == 1
        assert result.processed == 0

    def test_ingest_email_failed_attachment_sets_success_false(
        self, tmp_path: Path
    ) -> None:
        """Line 134: attachment that fails extraction sets success=False → no mark_processed."""
        from unittest.mock import patch

        from doc_automation.email_ingest.base import EmailMessage

        bad_pdf = tmp_path / "working" / "bad.pdf"
        bad_pdf.parent.mkdir(parents=True, exist_ok=True)
        bad_pdf.write_bytes(b"not a pdf")

        mock_email = MagicMock()
        mock_email.fetch_new.return_value = [
            EmailMessage(
                uid="99",
                subject="Bad Invoice",
                sender="x@x.com",
                received_at=datetime.now(tz=UTC),
                attachments=[bad_pdf],
            )
        ]

        pipeline = self._make_pipeline(tmp_path, email_source=mock_email)
        with patch("doc_automation.pipeline.extract_file", side_effect=ValueError("bad")):
            pipeline.run()

        mock_email.mark_processed.assert_not_called()

    def test_ingest_email_mark_processed_failure_logged(
        self, tmp_path: Path
    ) -> None:
        """Lines 139-140: mark_processed raises → warning logged, pipeline doesn't crash."""
        import fitz

        from doc_automation.email_ingest.base import EmailMessage

        pdf_path = tmp_path / "working" / "ok.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 100), "ACME Supplies Inc.\nTotal: $500.00")
        doc.save(str(pdf_path))

        mock_email = MagicMock()
        mock_email.fetch_new.return_value = [
            EmailMessage(
                uid="77",
                subject="Invoice",
                sender="bill@acme.com",
                received_at=datetime.now(tz=UTC),
                attachments=[pdf_path],
            )
        ]
        mock_email.mark_processed.side_effect = RuntimeError("IMAP closed")

        pipeline = self._make_pipeline(
            tmp_path, email_source=mock_email, templates_dir=Path("config/templates")
        )
        result = pipeline.run()  # must not raise
        assert result.processed >= 1

    # ── _process_attachment / _safe_extract paths ─────────────────────────────

    def test_safe_extract_parse_failure_quarantines(self, tmp_path: Path) -> None:
        """Lines 181-188: extract_file raises → file quarantined, errors incremented."""
        from unittest.mock import patch

        pdf_path = tmp_path / "bad.pdf"
        pdf_path.write_bytes(b"junk")

        audit_path = tmp_path / "audit.jsonl"
        audit = AuditLogger(audit_path)

        pipeline = self._make_pipeline(tmp_path, audit=audit)
        with patch("doc_automation.pipeline.extract_file", side_effect=ValueError("corrupt")):
            result = pipeline._safe_extract(pdf_path, email_id=None, result=PipelineResult())

        assert result is None
        quarantine = make_config(tmp_path).paths.quarantine_dir
        assert any(quarantine.iterdir())
        entries = [json.loads(ln) for ln in audit_path.read_text().strip().splitlines()]
        assert entries[0]["status"] == "parse_error"

    def test_blocking_anomaly_quarantines_invoice(self, tmp_path: Path) -> None:
        """Lines 156-157, 202-207: invoice with blocking flag → quarantined, audit logged."""
        from unittest.mock import patch

        import fitz

        pdf_path = tmp_path / "ok.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 100), "ACME Supplies Inc.\nTotal: $500.00")
        doc.save(str(pdf_path))

        audit_path = tmp_path / "audit.jsonl"
        audit = AuditLogger(audit_path)
        pipeline = self._make_pipeline(
            tmp_path, audit=audit, templates_dir=Path("config/templates")
        )

        with patch("doc_automation.pipeline.has_blocking_anomaly", return_value=True):
            result = pipeline._process_attachment(
                pdf_path, email_id=None, result=PipelineResult()
            )

        assert result is None
        quarantined = list(make_config(tmp_path).paths.quarantine_dir.iterdir())
        assert quarantined
        entries = [json.loads(ln) for ln in audit_path.read_text().strip().splitlines()]
        assert any(e["status"] == "blocked" for e in entries)

    def test_output_write_failure_queues_to_outbox_and_audits(
        self, tmp_path: Path
    ) -> None:
        """Lines 163-172: write_rows raises → invoice put in outbox, audit output_error."""
        import fitz

        pdf_path = tmp_path / "ok.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 100), "ACME Supplies Inc.\nTotal: $500.00")
        doc.save(str(pdf_path))

        audit_path = tmp_path / "audit.jsonl"
        audit = AuditLogger(audit_path)
        outbox = Outbox(tmp_path / "outbox.sqlite")
        mock_output = MagicMock()
        mock_output.write_rows.side_effect = OSError("sheets down")

        pipeline = self._make_pipeline(
            tmp_path,
            output_adapter=mock_output,
            audit=audit,
            outbox=outbox,
            templates_dir=Path("config/templates"),
        )
        result = pipeline._process_attachment(
            pdf_path, email_id=None, result=PipelineResult()
        )

        assert result is not None  # invoice returned even on output error
        assert len(outbox) == 1
        entries = [json.loads(ln) for ln in audit_path.read_text().strip().splitlines()]
        assert any(e["status"] == "output_error" for e in entries)

    def test_move_to_quarantine_handles_dest_collision(self, tmp_path: Path) -> None:
        """Lines 217-218: quarantine destination already exists → unique name used."""
        config = make_config(tmp_path)
        quarantine = config.paths.quarantine_dir
        quarantine.mkdir(parents=True, exist_ok=True)

        pdf_path = tmp_path / "invoice.pdf"
        pdf_path.write_bytes(b"data")
        (quarantine / "invoice.pdf").write_bytes(b"earlier copy")

        pipeline = self._make_pipeline(tmp_path)
        pipeline._move_to_quarantine(pdf_path)

        files = list(quarantine.iterdir())
        assert len(files) == 2  # original + renamed

    def test_move_to_quarantine_handles_move_error(self, tmp_path: Path) -> None:
        """Lines 219-222: shutil.move raises → warning logged, no crash."""
        from unittest.mock import patch

        pdf_path = tmp_path / "invoice.pdf"
        pdf_path.write_bytes(b"data")

        pipeline = self._make_pipeline(tmp_path)
        with patch("doc_automation.pipeline.shutil.move", side_effect=OSError("locked")):
            pipeline._move_to_quarantine(pdf_path)  # must not raise

    def test_drain_outbox_records_dedup(self, tmp_path: Path) -> None:
        """Line 109: dedup.record called when outbox drain succeeds."""
        outbox = Outbox(tmp_path / "outbox.sqlite")
        outbox.put(make_invoice(number="DEDUP-DR"), "original")

        mock_dedup = MagicMock()
        mock_output = MagicMock()
        mock_output.write_rows.return_value = 1

        pipeline = Pipeline(
            config=make_config(tmp_path),
            rules=make_rules(),
            coa=make_coa(),
            output_adapter=mock_output,
            outbox=outbox,
            dedup_db=mock_dedup,
            templates_dir=tmp_path / "templates",
        )
        result = pipeline.run()

        assert result.outbox_retried == 1
        mock_dedup.record.assert_called_once()

    def test_process_attachment_success_records_dedup_and_audits(
        self, tmp_path: Path
    ) -> None:
        """Lines 163, 165: dedup.record and audit logged on successful write_rows."""
        import fitz

        from doc_automation.dedup import DeduplicateDB

        pdf_path = tmp_path / "ok.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 100), "ACME Supplies Inc.\nInvoice No: INV-DA-01\nTotal: $500.00")
        doc.save(str(pdf_path))

        audit_path = tmp_path / "audit.jsonl"
        audit = AuditLogger(audit_path)
        dedup = DeduplicateDB(tmp_path / "dedup.sqlite")
        mock_output = MagicMock()
        mock_output.write_rows.return_value = 1

        pipeline = Pipeline(
            config=make_config(tmp_path),
            rules=make_rules(),
            coa=make_coa(),
            output_adapter=mock_output,
            audit_logger=audit,
            dedup_db=dedup,
            templates_dir=Path("config/templates"),
        )
        result = pipeline._process_attachment(
            pdf_path, email_id=None, result=PipelineResult()
        )

        assert result is not None
        entries = [json.loads(ln) for ln in audit_path.read_text().strip().splitlines()]
        assert any(e["status"] == "ok" for e in entries)
