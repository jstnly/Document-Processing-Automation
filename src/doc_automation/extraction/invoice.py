"""Invoice dataclass — the central data structure of the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path


@dataclass
class LineItem:
    description: str
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None


@dataclass
class Invoice:
    # ── Source metadata ──────────────────────────────────────────────────────
    source_file: Path
    template_used: str

    # ── Core fields (None = extraction did not find a value) ─────────────────
    vendor_name: str | None = None
    vendor_id: str | None = None          # derived slug, e.g. "acme-supplies-inc"
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str = "USD"
    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    total: Decimal | None = None
    line_items: list[LineItem] = field(default_factory=list)

    # ── Fields set by later pipeline stages ──────────────────────────────────
    gl_code: str | None = None
    anomaly_flags: list[str] = field(default_factory=list)
    source_email_id: str | None = None
    attachment_sha256: str | None = None
    processed_at: datetime | None = None

    def to_dict(self) -> dict[str, str]:
        """Serialise to a flat dict of strings for CSV/Excel output."""
        return {
            "vendor_name": self.vendor_name or "",
            "vendor_id": self.vendor_id or "",
            "invoice_number": self.invoice_number or "",
            "invoice_date": self.invoice_date.isoformat() if self.invoice_date else "",
            "due_date": self.due_date.isoformat() if self.due_date else "",
            "currency": self.currency,
            "subtotal": str(self.subtotal) if self.subtotal is not None else "",
            "tax_amount": str(self.tax_amount) if self.tax_amount is not None else "",
            "total": str(self.total) if self.total is not None else "",
            "gl_code": self.gl_code or "",
            "anomaly_flags": "; ".join(self.anomaly_flags),
            "template_used": self.template_used,
            "source_file": str(self.source_file.name),
            "source_email_id": self.source_email_id or "",
            "attachment_sha256": self.attachment_sha256 or "",
            "processed_at": (
                self.processed_at.isoformat() if self.processed_at else ""
            ),
        }
