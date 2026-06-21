"""Typed-ish loaders for the YAML configuration files in ``config/``.

Configuration is the single source of truth for the selected drug domain, signal
thresholds, and ingestion scope. Keeping it in versioned YAML (rather than in code)
satisfies the requirement that grouping / threshold logic be reproducible and
auditable (see requirements §6.4, §8, §9.6).
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any

import yaml

from .paths import CONFIG_DIR


@functools.lru_cache(maxsize=None)
def _load_yaml(filename: str) -> dict[str, Any]:
    path = CONFIG_DIR / filename
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# --------------------------------------------------------------------------- #
# Drug domain
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Drug:
    canonical_name: str
    drug_class: str
    class_display_name: str
    brands: tuple[str, ...]
    aliases: tuple[str, ...]


def load_drug_domain() -> list[Drug]:
    """Flatten ``drugs_of_interest.yml`` into a list of :class:`Drug`."""
    raw = _load_yaml("drugs_of_interest.yml")
    drugs: list[Drug] = []
    for class_key, class_block in raw.get("drug_classes", {}).items():
        display = class_block.get("display_name", class_key)
        for d in class_block.get("drugs", []):
            drugs.append(
                Drug(
                    canonical_name=d["canonical_name"],
                    drug_class=class_key,
                    class_display_name=display,
                    brands=tuple(d.get("brands", [])),
                    aliases=tuple(a.upper() for a in d.get("aliases", [])),
                )
            )
    return drugs


def alias_to_canonical() -> dict[str, str]:
    """Map every known raw uppercase alias/brand to its canonical drug name."""
    mapping: dict[str, str] = {}
    for drug in load_drug_domain():
        mapping[drug.canonical_name.upper()] = drug.canonical_name
        for alias in (*drug.aliases, *(b.upper() for b in drug.brands)):
            mapping[alias] = drug.canonical_name
    return mapping


def canonical_to_class() -> dict[str, str]:
    return {d.canonical_name: d.drug_class for d in load_drug_domain()}


# --------------------------------------------------------------------------- #
# Thresholds
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SignalThresholds:
    minimum_reports: int
    ror_lower_ci_threshold: float
    prr_threshold: float
    chi_square_threshold: float
    trend_baseline_quarters: int
    high_priority_score: float
    moderate_priority_score: float
    small_n_nhanes_warning: int
    very_small_n_nhanes_warning: int


def load_thresholds() -> SignalThresholds:
    raw = _load_yaml("signal_thresholds.yml")["signal_thresholds"]
    return SignalThresholds(**raw)


def load_priority_weights() -> dict[str, float]:
    weights = _load_yaml("signal_thresholds.yml")["priority_weights"]
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"priority_weights must sum to 1.0, got {total}")
    return weights


# --------------------------------------------------------------------------- #
# FAERS scope / PubMed / NHANES
# --------------------------------------------------------------------------- #
def load_faers_config() -> dict[str, Any]:
    return _load_yaml("faers_quarters.yml")


def load_pubmed_config() -> dict[str, Any]:
    return _load_yaml("pubmed_queries.yml")


def load_nhanes_config() -> dict[str, Any]:
    return _load_yaml("nhanes_variables.yml")
