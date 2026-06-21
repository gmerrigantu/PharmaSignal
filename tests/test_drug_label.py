"""Unit tests for the labeled-vs-novel matcher (pure logic, no network)."""
from pharmasignal.ingestion.drug_label import americanize, event_in_label


def _label(**sections) -> dict:
    return {"canonical": "x", "label_count": 1,
            "sections": {k: v.lower() for k, v in sections.items()}}


def test_americanize_british_spellings():
    assert americanize("diarrhoea") == "diarrhea"
    assert americanize("oesophageal") == "esophageal"
    assert americanize("haemorrhage") == "hemorrhage"
    assert americanize("tumour") == "tumor"
    assert americanize("oedema") == "edema"


def test_exact_substring_match():
    label = _label(adverse_reactions="Common reactions include nausea and vomiting.")
    labeled, section, found = event_in_label(label, "NAUSEA")
    assert labeled and found and section == "adverse_reactions"


def test_severity_ordering_prefers_boxed_warning():
    label = _label(
        boxed_warning="Risk of thyroid c-cell tumors.",
        adverse_reactions="tumors were observed.",
    )
    labeled, section, _ = event_in_label(label, "TUMORS")
    assert labeled and section == "boxed_warning"


def test_british_spelling_matches_american_label():
    # MedDRA "DIARRHOEA" must match a label that says "diarrhea".
    label = _label(adverse_reactions="The most common adverse reaction was diarrhea.")
    labeled, section, found = event_in_label(label, "DIARRHOEA")
    assert labeled and found


def test_all_words_present_match():
    label = _label(warnings_and_cautions="acute pancreatitis has been reported.")
    labeled, _, _ = event_in_label(label, "PANCREATITIS ACUTE")
    assert labeled


def test_novel_when_not_in_label():
    label = _label(adverse_reactions="nausea, vomiting, headache.")
    labeled, section, found = event_in_label(label, "CARDIOSPASM")
    assert found and not labeled and section is None


def test_unknown_when_no_label_retrieved():
    empty = {"canonical": "x", "label_count": 0, "sections": {}}
    labeled, section, found = event_in_label(empty, "NAUSEA")
    assert not found and not labeled
