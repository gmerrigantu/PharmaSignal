"""openFDA Drug Label API client — "labeled vs. novel" enrichment (requirements §3.2).

For each drug we fetch the structured product labeling and test whether a reported
adverse event already appears in the label's safety sections. This lets the dashboard
separate **already-labeled** reactions from **potentially novel** ones — the novel,
disproportionate signals are the interesting ones.

CAVEAT (see docs/limitations.md): labeling text is heterogeneous. Absence of a parsed
match is **not** proof the event is absent from official labeling — it is a
text-matching heuristic, surfaced as a prioritization hint, not a regulatory claim.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

import requests

from ..serving import storage

LABEL_URL = "https://api.fda.gov/drug/label.json"

# Safety sections, ordered by clinical severity. The first section that mentions the
# event is reported as the match (so a boxed-warning match outranks an adverse-reactions
# match).
SECTIONS_BY_SEVERITY = [
    "boxed_warning",
    "contraindications",
    "warnings_and_cautions",
    "warnings",
    "adverse_reactions",
    "precautions",
]


def fetch_label(canonical: str, aliases: tuple[str, ...], *, api_key: str = "",
                use_cache: bool = True, rxcui: str | None = None) -> dict:
    """Fetch + cache the merged safety-section text for one drug.

    Returns ``{"canonical", "sections": {name: lowercased_text}, "label_count",
    "source_url"}``. ``label_count == 0`` means no label was retrieved (→ "unknown",
    not "novel").

    When ``rxcui`` (an RxNorm ingredient RxCUI) is given it is added to the search as
    ``openfda.rxcui`` — an exact structured join that resolves the messy-name ambiguity
    the generic/brand text search suffers from on the whole-database universe.
    """
    terms = sorted({canonical.upper(), *(a.upper() for a in aliases)})
    clauses = [f'openfda.generic_name:"{t}" OR openfda.brand_name:"{t}"' for t in terms]
    if rxcui:
        clauses.append(f'openfda.rxcui:"{rxcui}"')
    search = " OR ".join(clauses)
    params: dict = {"search": search, "limit": 5}
    if api_key:
        params["api_key"] = api_key

    cache_uri = storage.bronze_uri(
        "drug_label", f"date={date.today().isoformat()}", f"{canonical}.json")
    if use_cache and storage.exists(cache_uri):
        return storage.read_json(cache_uri).get("data", {})

    resp = requests.get(LABEL_URL, params=params, timeout=30)
    if resp.status_code == 404:
        data = {"results": []}
    else:
        resp.raise_for_status()
        data = resp.json()

    # Merge each safety section's text across all returned labels (manufacturers vary).
    sections: dict[str, str] = {}
    for res in data.get("results", []):
        for sec in SECTIONS_BY_SEVERITY:
            val = res.get(sec)
            if not val:
                continue
            text = " ".join(val) if isinstance(val, list) else str(val)
            sections[sec] = (sections.get(sec, "") + " " + text.lower()).strip()

    out = {
        "canonical": canonical,
        "sections": sections,
        "label_count": len(data.get("results", [])),
        "source_url": resp.url,
    }
    storage.write_json(
        {"_ingest_timestamp": datetime.now(timezone.utc).isoformat(), "data": out},
        cache_uri,
    )
    return out


def americanize(s: str) -> str:
    """Map British MedDRA spellings to American label spellings.

    MedDRA preferred terms use British spelling ("DIARRHOEA", "OESOPHAGEAL",
    "HAEMORRHAGE", "TUMOUR"); FDA labels use American. Without this, well-known
    labeled reactions would be falsely flagged "novel".
    """
    return (s.replace("oe", "e").replace("ae", "e").replace("our", "or")
             .replace("oedema", "edema"))


def _candidates(term: str) -> list[tuple[str, list[str]]]:
    """Lowercased term variants (original + americanized) with their significant words."""
    out: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for variant in (term, americanize(term)):
        v = variant.strip()
        if v and v not in seen:
            seen.add(v)
            out.append((v, [w for w in re.findall(r"[a-z]+", v) if len(w) > 3]))
    return out


def event_in_label(label: dict, event_term: str) -> tuple[bool, str | None, bool]:
    """Test whether ``event_term`` appears in the label's safety sections.

    Returns ``(labeled, matched_section, label_found)``:
      - ``label_found`` is False when no label was retrieved → status "unknown".
      - ``labeled`` True with the most-severe matching section name otherwise.

    Matching heuristic: each lowercased term variant (original + American spelling) as a
    substring, OR all of its significant words (>3 chars) present in the section — to
    tolerate phrasing differences (e.g. "gastrooesophageal reflux disease" vs label
    "gastroesophageal reflux"). Transparent and easy to validate.
    """
    if not label or label.get("label_count", 0) == 0:
        return False, None, False

    candidates = _candidates((event_term or "").lower())
    for sec in SECTIONS_BY_SEVERITY:
        text = label["sections"].get(sec, "")
        if not text:
            continue
        for term, words in candidates:
            if (term and term in text) or (words and all(w in text for w in words)):
                return True, sec, True
    return False, None, True
