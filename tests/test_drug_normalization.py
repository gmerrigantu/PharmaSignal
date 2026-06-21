"""Unit tests for drug-name normalization (§8.1, §14.2)."""
from pharmasignal.transforms.normalize import (
    clean_drug_string,
    normalize_drug,
    normalize_reaction,
)


def test_clean_strips_dosage_and_form():
    cleaned = clean_drug_string("Ozempic 0.5 mg subcutaneous pen")
    assert "OZEMPIC" in cleaned
    assert "MG" not in cleaned and "0.5" not in cleaned


def test_brand_maps_to_canonical():
    n = normalize_drug("OZEMPIC")
    assert n.canonical == "semaglutide"
    assert n.drug_class == "glp1_receptor_agonists"
    assert n.method == "exact_dictionary"
    assert n.confidence == "high"


def test_brand_with_dose_maps_to_canonical():
    n = normalize_drug("Wegovy 2.4 mg injection")
    assert n.canonical == "semaglutide"


def test_generic_maps_to_canonical():
    assert normalize_drug("TIRZEPATIDE").canonical == "tirzepatide"
    assert normalize_drug("MOUNJARO").canonical == "tirzepatide"


def test_raw_name_is_preserved():
    raw = "Some Weird Brand 10 MG"
    n = normalize_drug(raw)
    assert n.raw == raw                 # never overwrite the source string
    assert n.canonical is None          # unmatched
    assert n.confidence == "low"
    assert n.method == "cleaned_unmatched"


def test_empty_input_is_unknown():
    n = normalize_drug("")
    assert n.normalized == "UNKNOWN"
    assert n.confidence == "unknown"


def test_reaction_normalization():
    assert normalize_reaction("  nausea ") == "NAUSEA"
    assert normalize_reaction("Pancreatitis  acute") == "PANCREATITIS ACUTE"
