"""
End-to-end integration tests.

These tests exercise the full pipeline using the real config/ directory and
the sample invoice in samples/invoices/.  They are heavier than unit tests
but give confidence that all stages wire together correctly.
"""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from doc_automation.audit import AuditLogger
from doc_automation.config import load_all_configs
from doc_automation.outbox import Outbox
from doc_automation.pipeline import Pipeline

# Config dir relative to the project root (two levels up from tests/)
CONFIG_DIR = Path(__file__).parent.parent / "config"
SAMPLE_INVOICE = Path(__file__).parent.parent / "samples" / "invoices" / "acme_sample.pdf"


def _make_invoice_pdf(tmp_path: Path, content: str) -> Path:
    """Create a minimal text PDF with the given content."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 100), content)
    path = tmp_path / "invoice.pdf"
    doc.save(str(path))
    return path


@pytest.fixture
def pipeline_with_csv(tmp_path):
    """Full Pipeline wired to real config + CSV output in tmp_path."""
    config, rules, coa = load_all_configs(CONFIG_DIR)
    csv_path = tmp_path / "out.csv"
    columns = [
        "vendor_name", "invoice_number", "invoice_date", "due_date",
        "currency", "subtotal", "tax_amount", "total",
        "gl_code", "anomaly_flags", "template_used", "source_file",
    ]
    from doc_automation.output.csv_writer import CSVAdapter
    output = CSVAdapter(file_path=csv_path, columns=columns)
    audit = AuditLogger(tmp_path / "audit.jsonl")
    outbox = Outbox(tmp_path / "outbox.sqlite")
    pl = Pipeline(
        config=config,
        rules=rules,
        coa=coa,
        output_adapter=output,
        audit_logger=audit,
        outbox=outbox,
        templates_dir=CONFIG_DIR / "templates",
    )
    yield pl, csv_path, tmp_path
    outbox.close()


class TestProcessFileSampleInvoice:
    """Smoke tests against the checked-in sample invoice."""

    @pytest.mark.skipif(
        not SAMPLE_INVOICE.exists(),
        reason="samples/invoices/acme_sample.pdf not present",
    )
    def test_extracts_core_fields(self, pipeline_with_csv):
        pl, csv_path, tmp_path = pipeline_with_csv
        invoice = pl.process_file(SAMPLE_INVOICE)

        assert invoice.vendor_name is not None
        assert "ACME" in invoice.vendor_name
        assert invoice.invoice_number == "INV-2024-0042"
        assert invoice.total == Decimal("1650.00")
        assert invoice.subtotal == Decimal("1500.00")
        assert invoice.tax_amount == Decimal("150.00")
        assert invoice.template_used == "acme-supplies"

    @pytest.mark.skipif(
        not SAMPLE_INVOICE.exists(),
        reason="samples/invoices/acme_sample.pdf not present",
    )
    def test_anomaly_flags_populated(self, pipeline_with_csv):
        """Regression: anomaly flags must be present after process_file (not empty)."""
        pl, _, _ = pipeline_with_csv
        invoice = pl.process_file(SAMPLE_INVOICE)
        # stale_date is expected since sample invoice date is Jan 2024
        assert "stale_date" in invoice.anomaly_flags

    @pytest.mark.skipif(
        not SAMPLE_INVOICE.exists(),
        reason="samples/invoices/acme_sample.pdf not present",
    )
    def test_csv_row_written(self, pipeline_with_csv):
        pl, csv_path, _ = pipeline_with_csv
        invoice = pl.process_file(SAMPLE_INVOICE)

        # Write manually via process_file result
        pl._output.write_rows([invoice])

        with open(csv_path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) >= 1
        row = next(r for r in rows if r["invoice_number"] == "INV-2024-0042")
        assert row["total"] == "1650.00"
        assert row["template_used"] == "acme-supplies"


class TestAnomalyFlagsWiredInPipeline:
    """Verifies the critical bug fix: run_anomaly_checks results reach invoice.anomaly_flags."""

    def test_amount_threshold_flag_attached(self, pipeline_with_csv, tmp_path):
        """An invoice over $10,000 must have amount_threshold in its flags."""
        pl, _, _ = pipeline_with_csv
        content = (
            "ACME Supplies Inc.\n"
            "Invoice No: INV-2024-HIGH\n"
            "Invoice Date: 2025-06-01\n"
            "Total: $25,000.00\n"
        )
        path = _make_invoice_pdf(tmp_path, content)
        invoice = pl.process_file(path)
        assert "amount_threshold" in invoice.anomaly_flags

    def test_missing_required_field_flag(self, pipeline_with_csv, tmp_path):
        """An invoice with no detectable total must trigger missing_required_field."""
        pl, _, _ = pipeline_with_csv
        content = "Some Company\nNo structured data here.\n"
        path = _make_invoice_pdf(tmp_path, content)
        invoice = pl.process_file(path)
        assert "missing_required_field" in invoice.anomaly_flags

    def test_no_spurious_flags_on_clean_invoice(self, pipeline_with_csv, tmp_path):
        """A recent, well-formed invoice within threshold must produce minimal flags."""
        pl, _, _ = pipeline_with_csv
        from datetime import date, timedelta
        recent = (date.today() - timedelta(days=5)).isoformat()
        content = (
            "ACME Supplies Inc.\n"
            f"Invoice No: INV-CLEAN-001\n"
            f"Invoice Date: {recent}\n"
            "Subtotal: $500.00\n"
            "Tax: $50.00\n"
            "Total: $550.00\n"
        )
        path = _make_invoice_pdf(tmp_path, content)
        invoice = pl.process_file(path)
        # Should not have stale_date, future_date, math_mismatch, amount_threshold
        unexpected = {"stale_date", "future_date", "math_mismatch_total", "amount_threshold"}
        overlap = unexpected & set(invoice.anomaly_flags)
        assert not overlap, f"Unexpected flags: {overlap}"


class TestAuditLogIntegration:
    def test_audit_entry_written_after_run(self, pipeline_with_csv, tmp_path):
        pl, _, work_dir = pipeline_with_csv
        content = (
            "Test Corp\nInvoice No: AU-001\n"
            "Invoice Date: 2025-01-01\nTotal: $100.00\n"
        )
        path = _make_invoice_pdf(tmp_path, content)
        invoice = pl.process_file(path)
        pl._audit.log_invoice(invoice, status="ok")

        audit_path = work_dir / "audit.jsonl"
        entries = [
            json.loads(line)
            for line in audit_path.read_text(encoding="utf-8").strip().splitlines()
        ]
        assert any(e["status"] == "ok" for e in entries)
        assert any(e.get("invoice_number") == "AU-001" for e in entries)

    def test_attachment_sha256_set(self, pipeline_with_csv, tmp_path):
        pl, _, _ = pipeline_with_csv
        path = _make_invoice_pdf(tmp_path, "ACME\nTotal: $100.00\n")
        invoice = pl.process_file(path)
        assert invoice.attachment_sha256 is not None
        assert len(invoice.attachment_sha256) == 64  # SHA-256 hex digest


class TestOutboxIntegration:
    def test_outbox_retried_on_run(self, pipeline_with_csv, tmp_path):
        pl, _, work_dir = pipeline_with_csv
        from decimal import Decimal

        from doc_automation.extraction.invoice import Invoice

        inv = Invoice(source_file=Path("retry.pdf"), template_used="_default")
        inv.vendor_name = "Retry Corp"
        inv.invoice_number = "RETRY-01"
        inv.total = Decimal("200.00")

        pl._outbox.put(inv, "connection refused")
        assert len(pl._outbox) == 1

        # Run drains the outbox — output adapter accepts the write
        result = pl.run()
        assert result.outbox_retried == 1
        assert len(pl._outbox) == 0
