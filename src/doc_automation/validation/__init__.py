"""Validation stage: COA matching + rule-based anomaly detection."""

from doc_automation.validation.anomaly import has_blocking_anomaly, run_anomaly_checks
from doc_automation.validation.coa import match_gl_code

__all__ = ["match_gl_code", "run_anomaly_checks", "has_blocking_anomaly"]
