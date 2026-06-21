"""Drug-name and reaction-term normalization.

FAERS drug names are messy. Per requirements §8 this is a first-class module:
raw names are always preserved; a normalized field is produced for analysis, and
the normalization *method* / *confidence* is tracked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import alias_to_canonical, canonical_to_class

# Dosage-form / noise suffixes that are safe to strip for matching.
_NOISE_PATTERNS = [
    r"\b\d+(\.\d+)?\s*(MG|MCG|ML|G|UNIT|UNITS|IU)\b",
    r"\b(TABLET|CAPSULE|INJECTION|SOLUTION|SUSPENSION|EXTENDED RELEASE|ER|XR|PEN)\b",
    r"\b(ORAL|SUBCUTANEOUS|IV|INTRAVENOUS)\b",
    r"\(.*?\)",            # parenthetical noise
    r"[^\w\s/-]",          # stray punctuation
]


@dataclass(frozen=True)
class NormalizedDrug:
    raw: str
    normalized: str            # canonical name if matched, else cleaned raw
    canonical: str | None      # canonical name when confidently matched
    drug_class: str | None
    method: str                # exact_dictionary | cleaned_unmatched | unknown
    confidence: str            # high | low | unknown


def clean_drug_string(raw: str) -> str:
    """Uppercase, strip dosage/form noise, collapse whitespace."""
    s = (raw or "").upper().strip()
    for pat in _NOISE_PATTERNS:
        s = re.sub(pat, " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_drug(raw: str) -> NormalizedDrug:
    """Map a raw FAERS drug string to a canonical drug + class when possible."""
    cleaned = clean_drug_string(raw)
    mapping = alias_to_canonical()
    class_map = canonical_to_class()

    # Try exact alias match on cleaned string, then on the first token (brand+dose).
    candidate = mapping.get(cleaned)
    if candidate is None and cleaned:
        first = cleaned.split(" ")[0]
        candidate = mapping.get(first)

    if candidate is not None:
        return NormalizedDrug(
            raw=raw,
            normalized=candidate,
            canonical=candidate,
            drug_class=class_map.get(candidate),
            method="exact_dictionary",
            confidence="high",
        )
    if cleaned:
        return NormalizedDrug(
            raw=raw,
            normalized=cleaned,
            canonical=None,
            drug_class=None,
            method="cleaned_unmatched",
            confidence="low",
        )
    return NormalizedDrug(
        raw=raw,
        normalized="UNKNOWN",
        canonical=None,
        drug_class=None,
        method="unknown",
        confidence="unknown",
    )


def normalize_reaction(raw: str) -> str:
    """Normalize a reaction term for joins/grouping (preserve raw separately)."""
    return re.sub(r"\s+", " ", (raw or "").upper().strip())
