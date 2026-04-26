"""
IMAP email source adapter.

Connects over IMAP4_SSL, searches for UNSEEN messages in the configured
folder, downloads qualifying attachments (PDF/images) to working_dir, and
optionally marks processed messages as Seen.
"""

from __future__ import annotations

import email as stdlib_email
import imaplib
import logging
import os
import re
from datetime import datetime, timezone
from email.header import decode_header, make_header
from pathlib import Path

from doc_automation.config import MailboxConfig
from doc_automation.email_ingest.base import EmailMessage, EmailSource

logger = logging.getLogger(__name__)

_SAFE_FILENAME = re.compile(r'[^\w\-.]')


def _decode_str(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value or ""


def _safe_filename(name: str) -> str:
    name = _SAFE_FILENAME.sub('_', name)
    return name[:200] or "attachment"


class IMAPSource(EmailSource):
    """Fetch invoices from an IMAP mailbox."""

    def __init__(self, config: MailboxConfig) -> None:
        self._config = config
        self._conn: imaplib.IMAP4_SSL | None = None

    def _connect(self) -> imaplib.IMAP4_SSL:
        if self._conn is not None:
            return self._conn
        username = os.environ.get(self._config.username_env, "")
        password = os.environ.get(self._config.password_env, "")
        if not username or not password:
            raise RuntimeError(
                f"IMAP credentials not set: {self._config.username_env!r} "
                f"and {self._config.password_env!r} env vars are required"
            )
        conn = imaplib.IMAP4_SSL(self._config.host, self._config.port)
        conn.login(username, password)
        self._conn = conn
        logger.debug("IMAP connected to %s as %s", self._config.host, username)
        return conn

    def fetch_new(self, working_dir: Path) -> list[EmailMessage]:
        working_dir.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        conn.select(self._config.inbox_folder)

        _status, data = conn.search(None, "UNSEEN")
        uids = (data[0] or b"").split()
        if not uids:
            return []

        allowed_types = set(self._config.filters.attachment_types)
        sender_allowlist = self._config.filters.sender_allowlist
        subject_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in self._config.filters.subject_patterns
        ]

        messages: list[EmailMessage] = []
        for uid_bytes in uids:
            uid = uid_bytes.decode()
            try:
                msg = self._fetch_message(conn, uid)
            except Exception:
                logger.exception("IMAP: failed to fetch UID %s", uid)
                continue

            subject = _decode_str(msg.get("Subject"))
            sender = _decode_str(msg.get("From"))

            if sender_allowlist and not any(
                s.lower() in sender.lower() for s in sender_allowlist
            ):
                logger.debug("IMAP: skipping UID %s — sender not in allowlist", uid)
                continue

            if subject_patterns and not any(p.search(subject) for p in subject_patterns):
                logger.debug("IMAP: skipping UID %s — subject not matched", uid)
                continue

            date_str = msg.get("Date", "")
            try:
                from email.utils import parsedate_to_datetime
                received_at = parsedate_to_datetime(date_str)
                if received_at.tzinfo is None:
                    received_at = received_at.replace(tzinfo=timezone.utc)
            except Exception:
                received_at = datetime.now(tz=timezone.utc)

            attachments = self._save_attachments(msg, uid, working_dir, allowed_types)
            if not attachments:
                logger.debug("IMAP: UID %s has no qualifying attachments", uid)
                continue

            messages.append(EmailMessage(
                uid=uid,
                subject=subject,
                sender=sender,
                received_at=received_at,
                attachments=attachments,
            ))

        logger.info("IMAP: fetched %d messages with attachments", len(messages))
        return messages

    def _fetch_message(
        self, conn: imaplib.IMAP4_SSL, uid: str
    ) -> stdlib_email.message.Message:
        _status, data = conn.fetch(uid, "(RFC822)")
        for part in data:
            if isinstance(part, tuple):
                return stdlib_email.message_from_bytes(part[1])
        raise ValueError(f"No RFC822 data for UID {uid}")

    def _save_attachments(
        self,
        msg: stdlib_email.message.Message,
        uid: str,
        working_dir: Path,
        allowed_types: set[str],
    ) -> list[Path]:
        saved: list[Path] = []
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = part.get_content_disposition() or ""
            if content_type not in allowed_types:
                continue
            if "attachment" not in disposition and "inline" not in disposition:
                continue
            filename = part.get_filename() or f"attachment_{uid}.bin"
            filename = _safe_filename(_decode_str(filename))
            dest = working_dir / f"{uid}_{filename}"
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            dest.write_bytes(payload)
            saved.append(dest)
            logger.debug("IMAP: saved attachment %s (%d bytes)", dest.name, len(payload))
        return saved

    def mark_processed(self, uid: str) -> None:
        if self._conn is None:
            return
        try:
            self._conn.store(uid, "+FLAGS", "\\Seen")
            logger.debug("IMAP: marked UID %s as Seen", uid)
        except Exception:
            logger.warning("IMAP: could not mark UID %s as Seen", uid, exc_info=True)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None
