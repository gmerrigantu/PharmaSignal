"""Unit tests for disproportionality, trend, and priority formulas (§14.2)."""
import math

import pytest

from pharmasignal.modeling import signal_scores as ss


def test_contingency_from_totals():
    c = ss.Contingency.from_totals(both=10, drug_total=100, event_total=200, all_total=10_000)
    assert c.a == 10
    assert c.b == 90          # drug_total - a
    assert c.c == 190         # event_total - a
    assert c.d == 10_000 - 10 - 90 - 190
    assert not c.needs_correction


def test_contingency_clamps_negative():
    # Inconsistent overlapping counts must clamp at 0, never go negative.
    c = ss.Contingency.from_totals(both=10, drug_total=5, event_total=5, all_total=10)
    assert c.b == 0 and c.c == 0 and c.d == 0
    assert c.needs_correction


def test_ror_known_value():
    # a=20 b=80 c=10 d=890 -> ROR = (20/80)/(10/890) = 0.25 / 0.011236 = 22.25
    c = ss.Contingency(a=20, b=80, c=10, d=890)
    ror, lo, hi = ss.reporting_odds_ratio(c)
    assert ror == pytest.approx((20 * 890) / (80 * 10), rel=1e-9)
    assert lo < ror < hi  # CI brackets the point estimate


def test_ror_ci_uses_delta_method():
    c = ss.Contingency(a=20, b=80, c=10, d=890)
    ror, lo, hi = ss.reporting_odds_ratio(c)
    se = math.sqrt(1 / 20 + 1 / 80 + 1 / 10 + 1 / 890)
    expected_lo = math.exp(math.log(ror) - ss.Z_95 * se)
    assert lo == pytest.approx(expected_lo, rel=1e-9)


def test_prr_known_value():
    c = ss.Contingency(a=20, b=80, c=10, d=890)
    prr = ss.proportional_reporting_ratio(c)
    expected = (20 / 100) / (10 / 900)
    assert prr == pytest.approx(expected, rel=1e-9)


def test_continuity_correction_applied_on_zero_cell():
    c = ss.Contingency(a=5, b=0, c=10, d=100)
    a, b, cc, d = c.corrected()
    assert (a, b, cc, d) == (5.5, 0.5, 10.5, 100.5)
    # ROR must be finite despite the zero cell.
    ror, lo, hi = ss.reporting_odds_ratio(c)
    assert math.isfinite(ror) and math.isfinite(lo) and math.isfinite(hi)


def test_expected_count_under_independence():
    c = ss.Contingency(a=10, b=90, c=190, d=9710)
    # E[a] = (drug_total * event_total)/total = (100 * 200)/10000 = 2.0
    assert ss.expected_count(c) == pytest.approx(2.0, rel=1e-9)


def test_shrinkage_downweights_small_counts():
    big = ss.Contingency.from_totals(100, 1000, 2000, 1_000_000)
    small = ss.Contingency.from_totals(2, 1000, 2000, 1_000_000)
    # Same expected OE direction, but small-n score is shrunk closer to 0.
    assert abs(ss.shrunken_log_score(small)) < abs(ss.shrunken_log_score(big))


def test_is_signal_rule():
    c = ss.Contingency(a=20, b=80, c=10, d=8000)
    disp = ss.disproportionality(c)
    assert ss.is_signal(
        c, disp, minimum_reports=3, ror_lower_ci_threshold=1.0,
        prr_threshold=2.0, chi_square_threshold=4.0)
    # Below minimum reports -> not a signal regardless of ratios.
    c2 = ss.Contingency(a=2, b=80, c=10, d=8000)
    disp2 = ss.disproportionality(c2)
    assert not ss.is_signal(
        c2, disp2, minimum_reports=3, ror_lower_ci_threshold=1.0,
        prr_threshold=2.0, chi_square_threshold=4.0)


def test_trend_metrics_detects_spike():
    t = ss.trend_metrics(current_count=40, baseline_counts=[10, 12, 11, 9])
    assert t.percent_change > 2.5
    assert t.z_score > 3
    assert 0.0 <= t.poisson_anomaly_score <= 1.0


def test_trend_metrics_flat_series():
    t = ss.trend_metrics(current_count=10, baseline_counts=[10, 10, 10, 10])
    assert t.percent_change == pytest.approx(0.0)
    # zero variance -> z-score undefined (NaN), handled gracefully downstream
    assert math.isnan(t.z_score)


def test_priority_score_weighting_and_bounds():
    weights = {
        "disproportionality": 0.30, "trend_anomaly": 0.25, "seriousness": 0.20,
        "literature_support": 0.15, "population_context": 0.10,
    }
    full = ss.priority_score(
        disproportionality_score=1, trend_anomaly_score=1, seriousness_score=1,
        literature_support_score=1, population_context_score=1, weights=weights)
    assert full == pytest.approx(1.0)
    none = ss.priority_score(
        disproportionality_score=0, trend_anomaly_score=0, seriousness_score=0,
        literature_support_score=0, population_context_score=0, weights=weights)
    assert none == pytest.approx(0.0)


def test_priority_level_thresholds():
    assert ss.priority_level(0.80, 0.75, 0.50) == "High"
    assert ss.priority_level(0.60, 0.75, 0.50) == "Moderate"
    assert ss.priority_level(0.10, 0.75, 0.50) == "Low"
