"""Gmail API email source — future implementation."""

from __future__ import annotations

from pathlib import Path

from doc_automation.email_ingest.base import EmailMessage, EmailSource


class GmailSource(EmailSource):
    """Fetch invoices via Gmail API (OAuth 2.0 service account)."""

    def fetch_new(self, working_dir: Path) -> list[EmailMessage]:
        raise NotImplementedError(
            "Gmail adapter is not yet implemented. "
            "Use adapter: imap with Gmail's IMAP settings as a workaround."
        )

    def mark_processed(self, uid: str) -> None:
        raise NotImplementedError
