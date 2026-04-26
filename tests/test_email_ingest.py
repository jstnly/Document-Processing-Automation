"""Tests for the IMAP email ingestion adapter (mock IMAP)."""

from __future__ import annotations

import email as stdlib_email
import email.mime.application
import email.mime.multipart
import email.mime.text
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from doc_automation.config import MailboxConfig, MailboxFilters
from doc_automation.email_ingest import build_email_source
from doc_automation.email_ingest.base import EmailMessage, EmailSource
from doc_automation.email_ingest.imap import IMAPSource


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_config(
    host: str = "imap.example.com",
    sender_allowlist: list[str] | None = None,
    subject_patterns: list[str] | None = None,
    attachment_types: list[str] | None = None,
) -> MailboxConfig:
    filters = MailboxFilters(
        sender_allowlist=sender_allowlist or [],
        subject_patterns=subject_patterns or [],
        attachment_types=attachment_types or [
            "application/pdf", "image/png", "image/jpeg", "image/tiff"
        ],
    )
    return MailboxConfig(
        adapter="imap",
        host=host,
        port=993,
        username_env="TEST_IMAP_USER",
        password_env="TEST_IMAP_PASS",
        inbox_folder="INBOX",
        filters=filters,
    )


def build_mime_message(
    subject: str = "Invoice from ACME",
    sender: str = "billing@acme.com",
    date: str = "Mon, 15 Jan 2024 10:00:00 +0000",
    pdf_content: bytes = b"%PDF-1.4 fake pdf content",
    filename: str = "invoice.pdf",
    content_type: str = "application/pdf",
) -> bytes:
    msg = email.mime.multipart.MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["Date"] = date
    msg.attach(email.mime.text.MIMEText("Please find the invoice attached."))
    attachment = email.mime.application.MIMEApplication(pdf_content, Name=filename)
    attachment["Content-Disposition"] = f'attachment; filename="{filename}"'
    attachment.replace_header("Content-Type", f"{content_type}; name=\"{filename}\"")
    msg.attach(attachment)
    return msg.as_bytes()


def make_mock_conn(
    uids: list[str],
    messages: dict[str, bytes],
) -> MagicMock:
    conn = MagicMock()
    conn.select.return_value = ("OK", [b"1"])
    uid_str = b" ".join(u.encode() for u in uids)
    conn.search.return_value = ("OK", [uid_str])

    def fetch_side_effect(uid, _fmt):
        raw = messages.get(uid, b"")
        return ("OK", [(b"1 (RFC822 {100})", raw)])

    conn.fetch.side_effect = fetch_side_effect
    conn.store.return_value = ("OK", [])
    conn.logout.return_value = ("BYE", [])
    return conn


# ── IMAPSource tests ──────────────────────────────────────────────────────────

class TestIMAPSource:
    def _make_source_with_mock_conn(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_conn: MagicMock,
    ) -> IMAPSource:
        monkeypatch.setenv("TEST_IMAP_USER", "user@example.com")
        monkeypatch.setenv("TEST_IMAP_PASS", "secret")
        source = IMAPSource(make_config())
        with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
            source._connect()
        return source

    def test_returns_empty_when_no_unseen(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        conn = make_mock_conn([], {})
        source = self._make_source_with_mock_conn(monkeypatch, conn)
        result = source.fetch_new(tmp_path)
        assert result == []

    def test_fetches_pdf_attachment(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        raw = build_mime_message(pdf_content=b"PDF-DATA-123")
        conn = make_mock_conn(["42"], {"42": raw})
        source = self._make_source_with_mock_conn(monkeypatch, conn)
        msgs = source.fetch_new(tmp_path)
        assert len(msgs) == 1
        assert msgs[0].uid == "42"
        assert msgs[0].subject == "Invoice from ACME"
        assert msgs[0].sender == "billing@acme.com"
        assert len(msgs[0].attachments) == 1
        assert msgs[0].attachments[0].read_bytes() == b"PDF-DATA-123"

    def test_attachment_saved_to_working_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        raw = build_mime_message()
        conn = make_mock_conn(["1"], {"1": raw})
        source = self._make_source_with_mock_conn(monkeypatch, conn)
        msgs = source.fetch_new(tmp_path)
        assert msgs[0].attachments[0].parent == tmp_path

    def test_creates_working_dir_if_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        raw = build_mime_message()
        conn = make_mock_conn(["1"], {"1": raw})
        source = self._make_source_with_mock_conn(monkeypatch, conn)
        new_dir = tmp_path / "new" / "subdir"
        source.fetch_new(new_dir)
        assert new_dir.exists()

    def test_skips_non_attachment_content_types(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        raw = build_mime_message(content_type="text/html", filename="document.html")
        conn = make_mock_conn(["5"], {"5": raw})
        source = self._make_source_with_mock_conn(monkeypatch, conn)
        msgs = source.fetch_new(tmp_path)
        assert msgs == []  # no qualifying attachment → message not included

    def test_multiple_messages(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        raw1 = build_mime_message(subject="Invoice A")
        raw2 = build_mime_message(subject="Invoice B", filename="b.pdf")
        conn = make_mock_conn(["10", "11"], {"10": raw1, "11": raw2})
        source = self._make_source_with_mock_conn(monkeypatch, conn)
        msgs = source.fetch_new(tmp_path)
        assert len(msgs) == 2
        subjects = {m.subject for m in msgs}
        assert subjects == {"Invoice A", "Invoice B"}

    def test_sender_allowlist_filters_out_unknown_sender(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("TEST_IMAP_USER", "user@example.com")
        monkeypatch.setenv("TEST_IMAP_PASS", "secret")
        config = make_config(sender_allowlist=["trusted@corp.com"])
        raw = build_mime_message(sender="spam@evil.com")
        conn = make_mock_conn(["3"], {"3": raw})
        source = IMAPSource(config)
        with patch("imaplib.IMAP4_SSL", return_value=conn):
            source._connect()
        msgs = source.fetch_new(tmp_path)
        assert msgs == []

    def test_sender_allowlist_allows_matching_sender(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("TEST_IMAP_USER", "user@example.com")
        monkeypatch.setenv("TEST_IMAP_PASS", "secret")
        config = make_config(sender_allowlist=["acme.com"])
        raw = build_mime_message(sender="billing@acme.com")
        conn = make_mock_conn(["4"], {"4": raw})
        source = IMAPSource(config)
        with patch("imaplib.IMAP4_SSL", return_value=conn):
            source._connect()
        msgs = source.fetch_new(tmp_path)
        assert len(msgs) == 1

    def test_subject_pattern_filters_non_matching(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("TEST_IMAP_USER", "u")
        monkeypatch.setenv("TEST_IMAP_PASS", "p")
        config = make_config(subject_patterns=["invoice|bill"])
        raw = build_mime_message(subject="Newsletter update")
        conn = make_mock_conn(["7"], {"7": raw})
        source = IMAPSource(config)
        with patch("imaplib.IMAP4_SSL", return_value=conn):
            source._connect()
        msgs = source.fetch_new(tmp_path)
        assert msgs == []

    def test_subject_pattern_matches(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("TEST_IMAP_USER", "u")
        monkeypatch.setenv("TEST_IMAP_PASS", "p")
        config = make_config(subject_patterns=["invoice"])
        raw = build_mime_message(subject="Invoice #2024-01")
        conn = make_mock_conn(["8"], {"8": raw})
        source = IMAPSource(config)
        with patch("imaplib.IMAP4_SSL", return_value=conn):
            source._connect()
        msgs = source.fetch_new(tmp_path)
        assert len(msgs) == 1

    def test_raises_without_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TEST_IMAP_USER", raising=False)
        monkeypatch.delenv("TEST_IMAP_PASS", raising=False)
        source = IMAPSource(make_config())
        with pytest.raises(RuntimeError, match="credentials not set"):
            source._connect()

    def test_mark_processed_calls_store(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        raw = build_mime_message()
        conn = make_mock_conn(["9"], {"9": raw})
        source = self._make_source_with_mock_conn(monkeypatch, conn)
        source.fetch_new(tmp_path)
        source.mark_processed("9")
        conn.store.assert_called_with("9", "+FLAGS", "\\Seen")

    def test_mark_processed_safe_without_connection(self) -> None:
        source = IMAPSource(make_config())
        source.mark_processed("99")  # should not raise

    def test_close_logs_out(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        raw = build_mime_message()
        conn = make_mock_conn(["2"], {"2": raw})
        source = self._make_source_with_mock_conn(monkeypatch, conn)
        source.fetch_new(tmp_path)
        source.close()
        conn.logout.assert_called_once()

    def test_close_idempotent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        raw = build_mime_message()
        conn = make_mock_conn(["2"], {"2": raw})
        source = self._make_source_with_mock_conn(monkeypatch, conn)
        source.fetch_new(tmp_path)
        source.close()
        source.close()  # second call should not raise
        assert conn.logout.call_count == 1

    def test_fetch_error_on_one_uid_continues_others(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        raw_good = build_mime_message(subject="Good Invoice")
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"2"])
        conn.search.return_value = ("OK", [b"10 11"])

        def fetch_side(uid, _fmt):
            if uid == "10":
                raise IOError("network error")
            return ("OK", [(b"1 (RFC822 {100})", raw_good)])

        conn.fetch.side_effect = fetch_side
        monkeypatch.setenv("TEST_IMAP_USER", "u")
        monkeypatch.setenv("TEST_IMAP_PASS", "p")
        source = IMAPSource(make_config())
        with patch("imaplib.IMAP4_SSL", return_value=conn):
            source._connect()
        msgs = source.fetch_new(tmp_path)
        assert len(msgs) == 1
        assert msgs[0].uid == "11"

    def test_received_at_is_datetime_with_timezone(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        raw = build_mime_message(date="Tue, 20 Feb 2024 14:30:00 +0500")
        conn = make_mock_conn(["6"], {"6": raw})
        source = self._make_source_with_mock_conn(monkeypatch, conn)
        msgs = source.fetch_new(tmp_path)
        assert msgs[0].received_at.tzinfo is not None
        assert msgs[0].received_at.year == 2024


# ── build_email_source factory ────────────────────────────────────────────────

class TestBuildEmailSource:
    def test_imap_returns_imap_source(self) -> None:
        cfg = make_config()
        src = build_email_source(cfg)
        assert isinstance(src, IMAPSource)

    def test_gmail_raises_not_implemented(self) -> None:
        cfg = MailboxConfig(adapter="gmail", host="")
        src = build_email_source(cfg)
        with pytest.raises(NotImplementedError):
            src.fetch_new(Path("/tmp"))

    def test_outlook_raises_not_implemented(self) -> None:
        cfg = MailboxConfig(adapter="outlook", host="")
        src = build_email_source(cfg)
        with pytest.raises(NotImplementedError):
            src.fetch_new(Path("/tmp"))
