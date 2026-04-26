"""Tests for COA matching and anomaly detection rules."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from doc_automation.config import AnomalyRule, AnomalyRulesConfig, COARow, DefaultsConfig
from doc_automation.extraction.invoice import Invoice, LineItem
from doc_automation.validation.anomaly import has_blocking_anomaly, run_anomaly_checks
from doc_automation.validation.coa import match_gl_code

CONFIG_DIR = Path(__file__).parent.parent / "config"


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_invoice(**kwargs: object) -> Invoice:
    defaults: dict = dict(
        source_file=Path("test.pdf"),
        template_used="_default",
        vendor_name="Test Vendor Inc.",
        vendor_id="test-vendor-inc",
        invoice_number="INV-001",
        invoice_date=date.today(),
        due_date=date.today() + timedelta(days=30),
        currency="USD",
        subtotal=Decimal("1000.00"),
        tax_amount=Decimal("100.00"),
        total=Decimal("1100.00"),
    )
    defaults.update(kwargs)
    return Invoice(**defaults)  # type: ignore[arg-type]


def _make_rules(*names_severities: tuple[str, str]) -> AnomalyRulesConfig:
    rules = [
        AnomalyRule(name=n, severity=s, description=f"{n} test rule")  # type: ignore[arg-type]
        for n, s in names_severities
    ]
    return AnomalyRulesConfig(rules=rules)


# ── COA matching ──────────────────────────────────────────────────────────────


def test_coa_matches_by_vendor_name() -> None:
    rows = [
        COARow(gl_code="6020", name="Software", vendor_match=".*microsoft.*"),
        COARow(gl_code="6999", name="Uncategorized", default_for_unmatched=True),
    ]
    inv = _make_invoice(vendor_name="Microsoft Corp")
    code = match_gl_code(inv, rows)
    assert code == "6020"
    assert "unknown_gl_code" not in inv.anomaly_flags


def test_coa_matches_by_keyword_fallback() -> None:
    rows = [
        COARow(gl_code="6010", name="Supplies", keyword_match=".*paper|toner.*"),
        COARow(gl_code="6999", name="Uncategorized", default_for_unmatched=True),
    ]
    inv = _make_invoice(
        vendor_name="Unknown Vendor",
        line_items=[LineItem(description="Laser Toner Cartridges", amount=Decimal("50.00"))],
    )
    code = match_gl_code(inv, rows)
    assert code == "6010"


def test_coa_falls_back_to_default() -> None:
    rows = [
        COARow(gl_code="6999", name="Uncategorized", default_for_unmatched=True),
    ]
    inv = _make_invoice(vendor_name="Mystery Vendor")
    code = match_gl_code(inv, rows)
    assert code == "6999"
    assert "unknown_gl_code" in inv.anomaly_flags


def test_coa_project_chart_loads_and_matches() -> None:
    from doc_automation.config import load_chart_of_accounts

    rows = load_chart_of_accounts(CONFIG_DIR / "chart_of_accounts.csv")
    # Microsoft should match Software & Subscriptions
    inv = _make_invoice(vendor_name="Microsoft Corp")
    code = match_gl_code(inv, rows)
    assert code == "6020"


# ── Anomaly rules ─────────────────────────────────────────────────────────────


def _defaults() -> DefaultsConfig:
    return DefaultsConfig(currency="USD", amount_threshold=10_000.0)


def test_no_anomalies_clean_invoice() -> None:
    from doc_automation.config import load_anomaly_rules

    rules = load_anomaly_rules(CONFIG_DIR / "anomaly_rules.yaml")
    inv = _make_invoice()
    flags = run_anomaly_checks(inv, rules, _defaults())
    assert flags == []


def test_amount_threshold_triggered() -> None:
    rules = _make_rules(("amount_threshold", "warn"))
    rules.rules[0].params = {"threshold": 5000}
    inv = _make_invoice(total=Decimal("6000.00"))
    flags = run_anomaly_checks(inv, rules, _defaults())
    assert "amount_threshold" in flags


def test_amount_threshold_not_triggered() -> None:
    rules = _make_rules(("amount_threshold", "warn"))
    rules.rules[0].params = {"threshold": 5000}
    inv = _make_invoice(total=Decimal("4999.99"))
    flags = run_anomaly_checks(inv, rules, _defaults())
    assert "amount_threshold" not in flags


def test_future_date_triggered() -> None:
    rules = _make_rules(("future_date", "warn"))
    inv = _make_invoice(invoice_date=date.today() + timedelta(days=1))
    flags = run_anomaly_checks(inv, rules, _defaults())
    assert "future_date" in flags


def test_future_date_not_triggered_today() -> None:
    rules = _make_rules(("future_date", "warn"))
    inv = _make_invoice(invoice_date=date.today())
    flags = run_anomaly_checks(inv, rules, _defaults())
    assert "future_date" not in flags


def test_stale_date_triggered() -> None:
    rules = _make_rules(("stale_date", "warn"))
    rules.rules[0].params = {"max_age_days": 180}
    inv = _make_invoice(invoice_date=date.today() - timedelta(days=200))
    flags = run_anomaly_checks(inv, rules, _defaults())
    assert "stale_date" in flags


def test_stale_date_not_triggered() -> None:
    rules = _make_rules(("stale_date", "warn"))
    rules.rules[0].params = {"max_age_days": 180}
    inv = _make_invoice(invoice_date=date.today() - timedelta(days=10))
    flags = run_anomaly_checks(inv, rules, _defaults())
    assert "stale_date" not in flags


def test_math_mismatch_total_triggered() -> None:
    rules = _make_rules(("math_mismatch_total", "warn"))
    rules.rules[0].params = {"tolerance": 0.02}
    inv = _make_invoice(subtotal=Decimal("1000"), tax_amount=Decimal("100"), total=Decimal("1200"))
    flags = run_anomaly_checks(inv, rules, _defaults())
    assert "math_mismatch_total" in flags


def test_math_mismatch_total_clean() -> None:
    rules = _make_rules(("math_mismatch_total", "warn"))
    rules.rules[0].params = {"tolerance": 0.02}
    inv = _make_invoice(subtotal=Decimal("1000"), tax_amount=Decimal("100"), total=Decimal("1100"))
    flags = run_anomaly_checks(inv, rules, _defaults())
    assert "math_mismatch_total" not in flags


def test_math_mismatch_subtotal_triggered() -> None:
    rules = _make_rules(("math_mismatch_subtotal", "warn"))
    rules.rules[0].params = {"tolerance": 0.02}
    inv = _make_invoice(
        subtotal=Decimal("500"),
        line_items=[
            LineItem(description="A", amount=Decimal("200")),
            LineItem(description="B", amount=Decimal("200")),
        ],
    )
    flags = run_anomaly_checks(inv, rules, _defaults())
    assert "math_mismatch_subtotal" in flags


def test_tax_rate_out_of_range_triggered() -> None:
    rules = _make_rules(("tax_rate_out_of_range", "warn"))
    rules.rules[0].params = {"min_rate": 0.0, "max_rate": 0.25}
    inv = _make_invoice(subtotal=Decimal("100"), tax_amount=Decimal("30"))  # 30%
    flags = run_anomaly_checks(inv, rules, _defaults())
    assert "tax_rate_out_of_range" in flags


def test_tax_rate_in_range_clean() -> None:
    rules = _make_rules(("tax_rate_out_of_range", "warn"))
    rules.rules[0].params = {"min_rate": 0.0, "max_rate": 0.25}
    inv = _make_invoice(subtotal=Decimal("100"), tax_amount=Decimal("10"))  # 10%
    flags = run_anomaly_checks(inv, rules, _defaults())
    assert "tax_rate_out_of_range" not in flags


def test_missing_required_field_triggered() -> None:
    rules = _make_rules(("missing_required_field", "warn"))
    inv = _make_invoice(invoice_number=None)
    flags = run_anomaly_checks(inv, rules, _defaults())
    assert "missing_required_field" in flags


def test_currency_mismatch_triggered() -> None:
    rules = _make_rules(("currency_mismatch", "warn"))
    inv = _make_invoice(currency="EUR")
    flags = run_anomaly_checks(inv, rules, _defaults())
    assert "currency_mismatch" in flags


def test_currency_mismatch_clean() -> None:
    rules = _make_rules(("currency_mismatch", "warn"))
    inv = _make_invoice(currency="USD")
    flags = run_anomaly_checks(inv, rules, _defaults())
    assert "currency_mismatch" not in flags


# ── Blocking severity ─────────────────────────────────────────────────────────


def test_has_blocking_anomaly_true() -> None:
    rules = _make_rules(("duplicate_invoice", "block"), ("amount_threshold", "warn"))
    assert has_blocking_anomaly(["duplicate_invoice"], rules) is True


def test_has_blocking_anomaly_false_for_warn() -> None:
    rules = _make_rules(("amount_threshold", "warn"))
    assert has_blocking_anomaly(["amount_threshold"], rules) is False


def test_has_blocking_anomaly_empty_flags() -> None:
    rules = _make_rules(("duplicate_invoice", "block"))
    assert has_blocking_anomaly([], rules) is False


# ── Full project rules smoke test ─────────────────────────────────────────────


def test_all_project_rules_run_on_clean_invoice() -> None:
    """Smoke test: all 11 project rules run without error on a valid invoice."""
    from doc_automation.config import load_anomaly_rules

    rules = load_anomaly_rules(CONFIG_DIR / "anomaly_rules.yaml")
    assert len(rules.rules) == 11
    inv = _make_invoice()
    flags = run_anomaly_checks(inv, rules, _defaults())
    assert "missing_required_field" not in flags
    assert "math_mismatch_total" not in flags
