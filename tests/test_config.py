"""Tests for configuration loading and validation."""

from pathlib import Path

import pytest

from doc_automation.config import (
    AnomalyRulesConfig,
    COARow,
    Config,
    ConfigError,
    load_all_configs,
    load_anomaly_rules,
    load_chart_of_accounts,
    load_config,
    load_output_config,
)

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"

# ── load_config ───────────────────────────────────────────────────────────────


def test_load_config_valid_project() -> None:
    """The project's own config.yaml must parse cleanly."""
    config = load_config(CONFIG_DIR / "config.yaml")
    assert isinstance(config, Config)
    assert config.defaults.currency == "USD"
    assert config.defaults.amount_threshold > 0
    assert config.mailbox is None  # not configured in default stub


def test_load_config_missing_file() -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(Path("/nonexistent/path/config.yaml"))


def test_load_config_imap_requires_host(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("mailbox:\n  adapter: imap\n")
    with pytest.raises(ConfigError, match="host"):
        load_config(tmp_path / "config.yaml")


def test_load_config_invalid_adapter(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("mailbox:\n  adapter: ftp\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path / "config.yaml")


def test_load_config_valid_imap(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        "mailbox:\n  adapter: imap\n  host: imap.example.com\n"
    )
    config = load_config(tmp_path / "config.yaml")
    assert config.mailbox is not None
    assert config.mailbox.host == "imap.example.com"


def test_load_config_defaults_apply(tmp_path: Path) -> None:
    """Empty config.yaml should still produce a valid Config with defaults."""
    (tmp_path / "config.yaml").write_text("{}\n")
    config = load_config(tmp_path / "config.yaml")
    assert config.defaults.currency == "USD"
    assert config.defaults.amount_threshold == 10_000.0


# ── load_anomaly_rules ────────────────────────────────────────────────────────


def test_load_anomaly_rules_project() -> None:
    rules = load_anomaly_rules(CONFIG_DIR / "anomaly_rules.yaml")
    assert isinstance(rules, AnomalyRulesConfig)
    assert len(rules.rules) == 11


def test_anomaly_rules_all_names_unique() -> None:
    rules = load_anomaly_rules(CONFIG_DIR / "anomaly_rules.yaml")
    names = [r.name for r in rules.rules]
    assert len(names) == len(set(names)), "Duplicate rule names found"


def test_anomaly_rules_severities_valid() -> None:
    rules = load_anomaly_rules(CONFIG_DIR / "anomaly_rules.yaml")
    for rule in rules.rules:
        assert rule.severity in {"info", "warn", "block"}


def test_anomaly_rules_missing_file() -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_anomaly_rules(Path("/no/such/file.yaml"))


# ── load_chart_of_accounts ────────────────────────────────────────────────────


def test_load_coa_project() -> None:
    rows = load_chart_of_accounts(CONFIG_DIR / "chart_of_accounts.csv")
    assert len(rows) >= 2
    defaults = [r for r in rows if r.default_for_unmatched]
    assert len(defaults) == 1, "Exactly one default-for-unmatched row required"


def test_coa_rejects_zero_defaults(tmp_path: Path) -> None:
    (tmp_path / "coa.csv").write_text(
        "gl_code,name,vendor_match,keyword_match,default_for_unmatched\n"
        "6010,Supplies,,,false\n"
    )
    with pytest.raises(ConfigError, match="default_for_unmatched"):
        load_chart_of_accounts(tmp_path / "coa.csv")


def test_coa_rejects_two_defaults(tmp_path: Path) -> None:
    (tmp_path / "coa.csv").write_text(
        "gl_code,name,vendor_match,keyword_match,default_for_unmatched\n"
        "6010,A,,,true\n"
        "6020,B,,,true\n"
    )
    with pytest.raises(ConfigError, match="default_for_unmatched"):
        load_chart_of_accounts(tmp_path / "coa.csv")


def test_coa_missing_file() -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_chart_of_accounts(Path("/no/such/file.csv"))


# ── load_output_config ────────────────────────────────────────────────────────


def test_load_output_config_project() -> None:
    data = load_output_config(CONFIG_DIR / "output.yaml")
    assert data["adapter"] in {"csv", "excel", "google_sheets"}


def test_output_config_rejects_missing_adapter(tmp_path: Path) -> None:
    (tmp_path / "out.yaml").write_text("columns: []\n")
    with pytest.raises(ConfigError, match="adapter"):
        load_output_config(tmp_path / "out.yaml")


def test_output_config_rejects_invalid_adapter(tmp_path: Path) -> None:
    (tmp_path / "out.yaml").write_text("adapter: kafka\n")
    with pytest.raises(ConfigError, match="invalid adapter"):
        load_output_config(tmp_path / "out.yaml")


# ── load_all_configs ──────────────────────────────────────────────────────────


def test_load_all_configs_project() -> None:
    """Full smoke test: every project config file must validate."""
    config, rules, coa = load_all_configs(CONFIG_DIR)
    assert isinstance(config, Config)
    assert isinstance(rules, AnomalyRulesConfig)
    assert isinstance(coa, list)
    assert all(isinstance(r, COARow) for r in coa)


def test_load_all_configs_collects_multiple_errors(tmp_path: Path) -> None:
    """Errors in multiple files are reported together, not one-at-a-time."""
    # config_dir with no files at all
    with pytest.raises(ConfigError) as exc_info:
        load_all_configs(tmp_path)
    msg = str(exc_info.value)
    # Should mention multiple issues (bullet points)
    assert msg.count("•") >= 2
