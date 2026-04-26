"""Configuration loading and validation (pydantic v2)."""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised when any configuration file fails to load or validate."""


# ── Pydantic models ───────────────────────────────────────────────────────────


class MailboxFilters(BaseModel):
    sender_allowlist: list[str] = []
    subject_patterns: list[str] = []
    attachment_types: list[str] = [
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
    ]


class MailboxConfig(BaseModel):
    adapter: Literal["imap", "gmail", "outlook"] = "imap"
    host: str = ""
    port: int = 993
    username_env: str = "MAILBOX_USER"
    password_env: str = "MAILBOX_PASS"
    inbox_folder: str = "INBOX"
    processed_label: str = "doc-automation/processed"
    filters: MailboxFilters = Field(default_factory=MailboxFilters)

    @model_validator(mode="after")
    def _host_required_for_imap(self) -> MailboxConfig:
        if self.adapter == "imap" and not self.host:
            raise ValueError("mailbox.host is required when adapter is 'imap'")
        return self


class DefaultsConfig(BaseModel):
    currency: str = "USD"
    amount_threshold: float = Field(default=10_000.0, gt=0)


class PathsConfig(BaseModel):
    working_dir: Path = Path("./working")
    quarantine_dir: Path = Path("./quarantine")
    audit_log: Path = Path("./logs/audit.jsonl")


class OutputRef(BaseModel):
    config_file: Path = Path("./config/output.yaml")


class Config(BaseModel):
    mailbox: MailboxConfig | None = None
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    output: OutputRef = Field(default_factory=OutputRef)


class AnomalyRule(BaseModel):
    name: str
    severity: Literal["info", "warn", "block"]
    description: str
    params: dict[str, Any] = Field(default_factory=dict)


class AnomalyRulesConfig(BaseModel):
    rules: list[AnomalyRule]


class COARow(BaseModel):
    gl_code: str
    name: str
    vendor_match: str = ""
    keyword_match: str = ""
    default_for_unmatched: bool = False


# ── Loaders ───────────────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ConfigError(
                f"{path}: expected a YAML mapping, got {type(data).__name__}"
            )
        return data
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: YAML parse error — {exc}") from exc


def load_config(path: Path) -> Config:
    data = _load_yaml(path)
    try:
        return Config.model_validate(data)
    except Exception as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def load_anomaly_rules(path: Path) -> AnomalyRulesConfig:
    data = _load_yaml(path)
    try:
        return AnomalyRulesConfig.model_validate(data)
    except Exception as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def load_chart_of_accounts(path: Path) -> list[COARow]:
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows: list[COARow] = []
            for i, row in enumerate(reader, start=2):
                raw = dict(row)
                raw["default_for_unmatched"] = (
                    raw.get("default_for_unmatched", "false").strip().lower() == "true"
                )
                try:
                    rows.append(COARow.model_validate(raw))
                except Exception as exc:
                    raise ConfigError(f"{path} row {i}: {exc}") from exc
    except FileNotFoundError:
        raise ConfigError(f"Chart of accounts not found: {path}") from None

    defaults = [r for r in rows if r.default_for_unmatched]
    if len(defaults) != 1:
        raise ConfigError(
            f"{path}: exactly one row must have default_for_unmatched=true "
            f"(found {len(defaults)})"
        )
    return rows


def load_output_config(path: Path) -> dict[str, Any]:
    data = _load_yaml(path)
    valid_adapters = {"csv", "excel", "google_sheets"}
    adapter = data.get("adapter")
    if not adapter:
        raise ConfigError(f"{path}: missing required key 'adapter'")
    if adapter not in valid_adapters:
        raise ConfigError(
            f"{path}: invalid adapter '{adapter}'; "
            f"must be one of {sorted(valid_adapters)}"
        )
    return data


def find_templates(templates_dir: Path) -> list[str]:
    if not templates_dir.exists():
        return []
    return sorted(p.stem for p in templates_dir.glob("*.yaml"))


def load_all_configs(
    config_dir: Path,
) -> tuple[Config, AnomalyRulesConfig, list[COARow]]:
    """Load and validate all config files; raise ConfigError listing all issues."""
    errors: list[str] = []
    config: Config | None = None
    rules: AnomalyRulesConfig | None = None
    coa: list[COARow] = []

    try:
        config = load_config(config_dir / "config.yaml")
    except ConfigError as exc:
        errors.append(str(exc))

    try:
        rules = load_anomaly_rules(config_dir / "anomaly_rules.yaml")
    except ConfigError as exc:
        errors.append(str(exc))

    try:
        coa = load_chart_of_accounts(config_dir / "chart_of_accounts.csv")
    except ConfigError as exc:
        errors.append(str(exc))

    # Resolve output config path from main config (if loaded)
    output_config_path = (
        Path(str(config.output.config_file)) if config else config_dir / "output.yaml"
    )
    # Make relative paths resolve from project root (parent of config_dir)
    if not output_config_path.is_absolute():
        output_config_path = config_dir.parent / output_config_path

    try:
        load_output_config(output_config_path)
    except ConfigError as exc:
        errors.append(str(exc))

    templates_default = config_dir / "templates" / "_default.yaml"
    if not templates_default.exists():
        errors.append(f"Missing required default template: {templates_default}")

    if errors:
        raise ConfigError("\n".join(f"  • {e}" for e in errors))

    assert config is not None  # guaranteed: errors would have been raised
    assert rules is not None
    return config, rules, coa
