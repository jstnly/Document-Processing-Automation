"""
Rule-based anomaly detection — all 11 rules from prompt.md §4.5.

Every check is a pure function keyed by rule name. The engine iterates
config/anomaly_rules.yaml at runtime, so users can add/remove/adjust rules
without changing code.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from doc_automation.config import AnomalyRule, AnomalyRulesConfig, DefaultsConfig
from doc_automation.extraction.invoice import Invoice

logger = logging.getLogger(__name__)


# ── Individual rule checks (pure functions) ───────────────────────────────────


def _check_duplicate_invoice(invoice: Invoice, audit_db: Path | None) -> bool:
    """True if (vendor_id, invoice_number) seen in audit log within last 365 days."""
    if not audit_db or not audit_db.exists():
        return False
    if not invoice.vendor_id or not invoice.invoice_number:
        return False
    try:
        con = sqlite3.connect(str(audit_db))
        cur = con.execute(
            """
            SELECT 1 FROM audit
            WHERE vendor_id = ? AND invoice_number = ?
              AND processed_at > datetime('now', '-365 days')
            LIMIT 1
            """,
            (invoice.vendor_id, invoice.invoice_number),
        )
        found = cur.fetchone() is not None
        con.close()
        return found
    except sqlite3.Error as exc:
        logger.warning("Could not check duplicate invoice: %s", exc)
        return False


def _check_amount_threshold(invoice: Invoice, rule: AnomalyRule) -> bool:
    if invoice.total is None:
        return False
    threshold = Decimal(str(rule.params.get("threshold", 10_000)))
    return invoice.total > threshold


def _check_future_date(invoice: Invoice) -> bool:
    if invoice.invoice_date is None:
        return False
    return invoice.invoice_date > date.today()


def _check_stale_date(invoice: Invoice, rule: AnomalyRule) -> bool:
    if invoice.invoice_date is None:
        return False
    max_age = int(rule.params.get("max_age_days", 180))
    delta = date.today() - invoice.invoice_date
    return delta.days > max_age


def _check_math_mismatch_subtotal(invoice: Invoice, rule: AnomalyRule) -> bool:
    if not invoice.line_items or invoice.subtotal is None:
        return False
    tolerance = Decimal(str(rule.params.get("tolerance", "0.02")))
    line_sum = sum(
        (item.amount for item in invoice.line_items if item.amount is not None),
        Decimal("0"),
    )
    return abs(line_sum - invoice.subtotal) > tolerance


def _check_math_mismatch_total(invoice: Invoice, rule: AnomalyRule) -> bool:
    if invoice.subtotal is None or invoice.tax_amount is None or invoice.total is None:
        return False
    tolerance = Decimal(str(rule.params.get("tolerance", "0.02")))
    expected = invoice.subtotal + invoice.tax_amount
    return abs(expected - invoice.total) > tolerance


def _check_tax_rate_out_of_range(invoice: Invoice, rule: AnomalyRule) -> bool:
    if invoice.subtotal is None or invoice.tax_amount is None:
        return False
    if invoice.subtotal == Decimal("0"):
        return False
    rate = invoice.tax_amount / invoice.subtotal
    min_rate = Decimal(str(rule.params.get("min_rate", "0.0")))
    max_rate = Decimal(str(rule.params.get("max_rate", "0.25")))
    return not (min_rate <= rate <= max_rate)


def _check_missing_required_field(invoice: Invoice) -> bool:
    required = (
        invoice.vendor_name,
        invoice.invoice_number,
        invoice.invoice_date,
        invoice.total,
    )
    return any(v is None for v in required)


def _check_currency_mismatch(invoice: Invoice, defaults: DefaultsConfig) -> bool:
    return invoice.currency.upper() != defaults.currency.upper()


# ── Engine ────────────────────────────────────────────────────────────────────

_RULE_CHECKS = {
    "duplicate_invoice",
    "amount_threshold",
    "future_date",
    "stale_date",
    "math_mismatch_subtotal",
    "math_mismatch_total",
    "tax_rate_out_of_range",
    "missing_required_field",
    "unknown_vendor",       # set during extraction, not here
    "unknown_gl_code",      # set during COA matching, not here
    "currency_mismatch",
}


def run_anomaly_checks(
    invoice: Invoice,
    rules_config: AnomalyRulesConfig,
    defaults: DefaultsConfig,
    audit_db: Path | None = None,
) -> list[str]:
    """
    Run all enabled anomaly rules against the invoice.

    Returns a list of triggered rule names (not adding them to invoice.anomaly_flags —
    the caller decides what to do with them).
    """
    triggered: list[str] = []

    for rule in rules_config.rules:
        name = rule.name

        # unknown_vendor / unknown_gl_code are set by earlier stages
        if name in ("unknown_vendor", "unknown_gl_code"):
            continue

        fired = False
        try:
            if name == "duplicate_invoice":
                fired = _check_duplicate_invoice(invoice, audit_db)
            elif name == "amount_threshold":
                fired = _check_amount_threshold(invoice, rule)
            elif name == "future_date":
                fired = _check_future_date(invoice)
            elif name == "stale_date":
                fired = _check_stale_date(invoice, rule)
            elif name == "math_mismatch_subtotal":
                fired = _check_math_mismatch_subtotal(invoice, rule)
            elif name == "math_mismatch_total":
                fired = _check_math_mismatch_total(invoice, rule)
            elif name == "tax_rate_out_of_range":
                fired = _check_tax_rate_out_of_range(invoice, rule)
            elif name == "missing_required_field":
                fired = _check_missing_required_field(invoice)
            elif name == "currency_mismatch":
                fired = _check_currency_mismatch(invoice, defaults)
            else:
                logger.warning("Unknown anomaly rule '%s' — skipping", name)
        except Exception as exc:
            logger.error("Error evaluating rule '%s': %s", name, exc)

        if fired:
            triggered.append(name)
            logger.debug("Anomaly rule fired: %s for %s", name, invoice.invoice_number)

    return triggered


def has_blocking_anomaly(flags: list[str], rules_config: AnomalyRulesConfig) -> bool:
    """Return True if any flag in the list corresponds to a block-severity rule."""
    severity_map = {r.name: r.severity for r in rules_config.rules}
    return any(severity_map.get(f) == "block" for f in flags)
