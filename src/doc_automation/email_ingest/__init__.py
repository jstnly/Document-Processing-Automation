"""Email ingestion adapters."""

from __future__ import annotations

from doc_automation.config import MailboxConfig
from doc_automation.email_ingest.base import EmailMessage, EmailSource
from doc_automation.email_ingest.gmail import GmailSource
from doc_automation.email_ingest.imap import IMAPSource
from doc_automation.email_ingest.outlook import OutlookSource


def build_email_source(config: MailboxConfig) -> EmailSource:
    """Instantiate the configured EmailSource from a MailboxConfig."""
    if config.adapter == "imap":
        return IMAPSource(config)
    if config.adapter == "gmail":
        return GmailSource()
    if config.adapter == "outlook":
        return OutlookSource()
    raise ValueError(
        f"Unknown email adapter '{config.adapter}'. "
        "Valid choices: imap, gmail, outlook"
    )


__all__ = [
    "EmailMessage",
    "EmailSource",
    "IMAPSource",
    "GmailSource",
    "OutlookSource",
    "build_email_source",
]
