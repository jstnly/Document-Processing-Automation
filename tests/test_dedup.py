"""Tests for DeduplicateDB and the duplicate_invoice anomaly rule wiring."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from doc_automation.config import AnomalyRule, AnomalyRulesConfig, DefaultsConfig
from doc_automation.dedup import DeduplicateDB
from doc_automation.extraction.invoice import Invoice
from doc_automation.validation.anomaly import run_anomaly_checks


def make_invoice(
    vendor_id: str = "acme-inc",
    number: str = "INV-001",
) -> Invoice:
    inv = Invoice(source_file=Path("test.pdf"), template_used="_default")
    inv.vendor_id = vendor_id
    inv.vendor_name = "ACME Inc."
    inv.invoice_number = number
    inv.invoice_date = date.today()
    inv.total = Decimal("500.00")
    inv.processed_at = datetime.now(tz=UTC)
    return inv


def make_rules_with_duplicate() -> AnomalyRulesConfig:
    return AnomalyRulesConfig(rules=[
        AnomalyRule(
            name="duplicate_invoice",
            severity="block",
            description="Duplicate invoice",
            params={},
        )
    ])


class TestDeduplicateDB:
    def test_new_invoice_not_duplicate(self, tmp_path: Path) -> None:
        db = DeduplicateDB(tmp_path / "dedup.sqlite")
        assert not db.is_duplicate("acme", "INV-001")

    def test_recorded_invoice_is_duplicate(self, tmp_path: Path) -> None:
        db = DeduplicateDB(tmp_path / "dedup.sqlite")
        inv = make_invoice()
        db.record(inv)
        assert db.is_duplicate("acme-inc", "INV-001")

    def test_different_number_not_duplicate(self, tmp_path: Path) -> None:
        db = DeduplicateDB(tmp_path / "dedup.sqlite")
        db.record(make_invoice(number="INV-001"))
        assert not db.is_duplicate("acme-inc", "INV-002")

    def test_different_vendor_not_duplicate(self, tmp_path: Path) -> None:
        db = DeduplicateDB(tmp_path / "dedup.sqlite")
        db.record(make_invoice(vendor_id="acme-inc", number="INV-001"))
        assert not db.is_duplicate("widgets-co", "INV-001")

    def test_record_missing_fields_is_noop(self, tmp_path: Path) -> None:
        db = DeduplicateDB(tmp_path / "dedup.sqlite")
        inv = Invoice(source_file=Path("x.pdf"), template_used="_default")
        # no vendor_id or invoice_number
        db.record(inv)  # must not raise
        assert not db.is_duplicate("", "")

    def test_days_window_excludes_old_entries(self, tmp_path: Path) -> None:
        db = DeduplicateDB(tmp_path / "dedup.sqlite")
        # Manually insert an old entry (no need to create a full Invoice here)
        old_ts = (datetime.now(tz=UTC) - timedelta(days=400)).isoformat()
        db._conn.execute(
            "INSERT INTO seen_invoices (vendor_id, invoice_number, processed_at) VALUES (?, ?, ?)",
            ("acme-inc", "INV-001", old_ts),
        )
        db._conn.commit()
        assert not db.is_duplicate("acme-inc", "INV-001", days=365)

    def test_days_window_includes_recent_entries(self, tmp_path: Path) -> None:
        db = DeduplicateDB(tmp_path / "dedup.sqlite")
        db.record(make_invoice())
        assert db.is_duplicate("acme-inc", "INV-001", days=30)

    def test_persist_across_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "dedup.sqlite"
        db1 = DeduplicateDB(path)
        db1.record(make_invoice())
        db1.close()

        db2 = DeduplicateDB(path)
        assert db2.is_duplicate("acme-inc", "INV-001")
        db2.close()

    def test_close_then_del_safe(self, tmp_path: Path) -> None:
        db = DeduplicateDB(tmp_path / "dedup.sqlite")
        db.close()
        del db  # __del__ should not raise after explicit close

    def test_path_property_returns_db_path(self, tmp_path: Path) -> None:
        """dedup.py:54 — path property returns the database file path."""
        db_path = tmp_path / "dedup.sqlite"
        db = DeduplicateDB(db_path)
        assert db.path == db_path


class TestDuplicateInvoiceRule:
    def test_no_dedup_db_never_fires(self) -> None:
        inv = make_invoice()
        rules = make_rules_with_duplicate()
        flags = run_anomaly_checks(inv, rules, DefaultsConfig(), dedup_db=None)
        assert "duplicate_invoice" not in flags

    def test_fires_when_duplicate_recorded(self, tmp_path: Path) -> None:
        db = DeduplicateDB(tmp_path / "dedup.sqlite")
        inv = make_invoice()
        db.record(inv)

        inv2 = make_invoice()  # same vendor_id + number
        rules = make_rules_with_duplicate()
        flags = run_anomaly_checks(inv2, rules, DefaultsConfig(), dedup_db=db)
        assert "duplicate_invoice" in flags

    def test_does_not_fire_for_new_invoice(self, tmp_path: Path) -> None:
        db = DeduplicateDB(tmp_path / "dedup.sqlite")
        inv = make_invoice()
        rules = make_rules_with_duplicate()
        flags = run_anomaly_checks(inv, rules, DefaultsConfig(), dedup_db=db)
        assert "duplicate_invoice" not in flags

    def test_missing_vendor_id_does_not_fire(self, tmp_path: Path) -> None:
        db = DeduplicateDB(tmp_path / "dedup.sqlite")
        inv = make_invoice()
        inv.vendor_id = None  # type: ignore[assignment]
        rules = make_rules_with_duplicate()
        flags = run_anomaly_checks(inv, rules, DefaultsConfig(), dedup_db=db)
        assert "duplicate_invoice" not in flags


class TestIMAPRetry:
    """Tests for the _with_retry helper in imap.py."""

    def test_retry_succeeds_on_second_attempt(self) -> None:
        import imaplib

        from doc_automation.email_ingest.imap import _with_retry

        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise imaplib.IMAP4.error("temporary failure")
            return "ok"

        result = _with_retry(flaky, attempts=3)
        assert result == "ok"
        assert call_count == 2

    def test_retry_raises_after_all_attempts(self) -> None:
        import imaplib

        from doc_automation.email_ingest.imap import _with_retry

        def always_fails():
            raise imaplib.IMAP4.error("permanent failure")

        with pytest.raises(imaplib.IMAP4.error, match="permanent"):
            _with_retry(always_fails, attempts=3)

    def test_retry_not_triggered_for_other_exceptions(self) -> None:
        from doc_automation.email_ingest.imap import _with_retry

        call_count = 0

        def raises_value_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("not a network error")

        with pytest.raises(ValueError):
            _with_retry(raises_value_error, attempts=3)
        # ValueError is not in the caught exceptions, so it should propagate immediately
        assert call_count == 1

    def test_no_sleep_on_first_attempt_success(self) -> None:
        import time

        from doc_automation.email_ingest.imap import _with_retry

        start = time.monotonic()
        result = _with_retry(lambda: 42, attempts=3)
        elapsed = time.monotonic() - start
        assert result == 42
        assert elapsed < 0.1  # no sleep on success
