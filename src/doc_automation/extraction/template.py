"""Vendor template loading, validation, and selection."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from doc_automation.extraction.utils import parse_re_flags

logger = logging.getLogger(__name__)


class FieldConfig(BaseModel):
    strategy: Literal["regex", "anchor", "table"] = "regex"
    # ── regex params ─────────────────────────────────────────────────────────
    pattern: str = ""
    flags: str = ""
    default: str | None = None
    # ── anchor params ────────────────────────────────────────────────────────
    anchor: str = ""
    direction: Literal["right", "left", "above", "below"] = "right"
    max_distance: float = 200.0
    # ── table params (line items) ────────────────────────────────────────────
    header_pattern: str = ""
    columns: list[str] = Field(default_factory=list)


class VendorTemplate(BaseModel):
    name: str = ""                          # set from file stem after load
    match: str                              # regex to identify this vendor
    priority: int = 0
    fields: dict[str, FieldConfig] = Field(default_factory=dict)

    def matches(self, text: str) -> bool:
        """Return True if this template's match regex hits the text."""
        try:
            return bool(re.search(self.match, text, re.IGNORECASE))
        except re.error as exc:
            logger.warning("Bad match regex in template '%s': %s", self.name, exc)
            return False


def load_template(path: Path) -> VendorTemplate:
    """Load a single vendor template YAML file."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        raise FileNotFoundError(f"Template not found: {path}") from None
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: YAML error — {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping")

    try:
        tmpl = VendorTemplate.model_validate(data)
    except Exception as exc:
        raise ValueError(f"{path}: {exc}") from exc

    tmpl.name = path.stem

    # Validate regex patterns eagerly so bad templates fail at startup
    for field_name, cfg in tmpl.fields.items():
        if cfg.strategy == "regex" and cfg.pattern:
            try:
                flags = parse_re_flags(cfg.flags)
                re.compile(cfg.pattern, flags)
            except (re.error, ValueError) as exc:
                raise ValueError(
                    f"{path} field '{field_name}': invalid regex — {exc}"
                ) from exc

    return tmpl


def load_all_templates(templates_dir: Path) -> list[VendorTemplate]:
    """
    Load all *.yaml files from templates_dir.

    Returns templates sorted by priority descending (highest first), then
    alphabetically for stability. The _default template is always last.
    """
    templates: list[VendorTemplate] = []
    for path in sorted(templates_dir.glob("*.yaml")):
        try:
            templates.append(load_template(path))
        except (ValueError, FileNotFoundError) as exc:
            logger.error("Failed to load template %s: %s", path.name, exc)

    templates.sort(key=lambda t: (-t.priority, t.name))
    logger.debug(
        "Loaded %d templates from %s (order: %s)",
        len(templates),
        templates_dir,
        [t.name for t in templates],
    )
    return templates


def select_template(text: str, templates: list[VendorTemplate]) -> VendorTemplate:
    """
    Return the first template (by priority) whose match regex hits text.

    Always returns something as long as _default.yaml (match='.*') is present.
    Raises ValueError only if the template list is empty.
    """
    if not templates:
        raise ValueError(
            "No templates loaded — is config/templates/_default.yaml present?"
        )
    for tmpl in templates:
        if tmpl.matches(text):
            logger.debug("Template selected: %s", tmpl.name)
            return tmpl
    # Unreachable if _default.yaml with match='.*' is present, but defensive:
    raise ValueError("No template matched document text")
