"""Unit tests for openFDA query-clause builders used by subgroup/interaction marts."""
from pharmasignal.ingestion import openfda as o


def test_sex_clause():
    assert o.sex_clause(1) == "patient.patientsex:1"
    assert o.sex_clause(2) == "patient.patientsex:2"


def test_age_clause_restricts_to_years_and_range():
    clause = o.age_clause(65, 120)
    assert "patient.patientonsetageunit:801" in clause       # years only
    assert "patient.patientonsetage:[65 TO 120]" in clause
    assert " AND " in clause


def test_event_and_drug_clause_quote_terms():
    assert o.event_clause("PANCREATITIS ACUTE") == 'patient.reaction.reactionmeddrapt.exact:"PANCREATITIS ACUTE"'
    assert o.drug_clause("ozempic") == 'patient.drug.medicinalproduct.exact:"OZEMPIC"'


def test_and_query_wraps_each_clause():
    q = o.and_query(["a:1", "b:2"])
    assert q == "(a:1) AND (b:2)"
