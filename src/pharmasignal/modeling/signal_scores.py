"""Disproportionality and trend statistics for drug-event pairs.

Every function here is PURE (no I/O, no network) so the formulas can be unit-tested
in isolation — see tests/test_signal_scores.py and requirements §9, §14.2.

IMPORTANT FRAMING: ROR, PRR, the shrinkage score, and the composite priority score
are *signal-prioritization* metrics computed from spontaneous reports. They are
hypothesis-generating only and do **not** establish causality, clinical risk, or
incidence. This caveat is surfaced on every dashboard page.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# 95% two-sided normal quantile, used for ROR confidence intervals.
Z_95 = 1.959963984540054


@dataclass(frozen=True)
class Contingency:
    """2x2 reporting contingency table for one drug-event pair.

    ::

                         event            other events
        drug of interest a                b
        other drugs      c                d
    """

    a: int  # reports with this drug AND this event
    b: int  # reports with this drug AND other events
    c: int  # reports with other drugs AND this event
    d: int  # reports with other drugs AND other events

    @classmethod
    def from_totals(
        cls, both: int, drug_total: int, event_total: int, all_total: int
    ) -> "Contingency":
        """Build the table from marginal counts (the openFDA count pattern).

        ``b``/``c``/``d`` are clamped at 0 to absorb the rare case where overlapping
        openFDA counts are momentarily inconsistent across cached queries.
        """
        a = both
        b = max(drug_total - a, 0)
        c = max(event_total - a, 0)
        d = max(all_total - a - b - c, 0)
        return cls(a=a, b=b, c=c, d=d)

    @property
    def needs_correction(self) -> bool:
        """Any zero cell would make ROR/PRR undefined -> apply 0.5 continuity."""
        return any(cell == 0 for cell in (self.a, self.b, self.c, self.d))

    def corrected(self) -> tuple[float, float, float, float]:
        """Return cells with Haldane-Anscombe 0.5 correction if any cell is zero."""
        if self.needs_correction:
            return self.a + 0.5, self.b + 0.5, self.c + 0.5, self.d + 0.5
        return float(self.a), float(self.b), float(self.c), float(self.d)


# --------------------------------------------------------------------------- #
# Disproportionality
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DisproportionalityResult:
    ror: float
    ror_ci_lower: float
    ror_ci_upper: float
    prr: float
    chi_square: float
    expected_a: float
    oe_ratio: float            # observed / expected
    shrunken_log_score: float  # simplified empirical Bayes shrinkage
    continuity_correction: bool


def reporting_odds_ratio(c: Contingency) -> tuple[float, float, float]:
    """ROR and its 95% CI.

    ROR = (a/b) / (c/d); CI via the delta method on log(ROR):
    SE(log ROR) = sqrt(1/a + 1/b + 1/c + 1/d).
    """
    a, b, cc, d = c.corrected()
    ror = (a * d) / (b * cc)
    se = math.sqrt(1 / a + 1 / b + 1 / cc + 1 / d)
    log_ror = math.log(ror)
    lower = math.exp(log_ror - Z_95 * se)
    upper = math.exp(log_ror + Z_95 * se)
    return ror, lower, upper


def proportional_reporting_ratio(c: Contingency) -> float:
    """PRR = [a/(a+b)] / [c/(c+d)]."""
    a, b, cc, d = c.corrected()
    return (a / (a + b)) / (cc / (cc + d))


def chi_square_yates(c: Contingency) -> float:
    """Yates-corrected chi-square for the 2x2 table (matches PRR signal rule)."""
    a, b, cc, d = float(c.a), float(c.b), float(c.c), float(c.d)
    n = a + b + cc + d
    if n == 0:
        return 0.0
    row1, row2 = a + b, cc + d
    col1, col2 = a + cc, b + d
    if min(row1, row2, col1, col2) == 0:
        return 0.0
    numerator = n * (abs(a * d - b * cc) - n / 2) ** 2
    denominator = row1 * row2 * col1 * col2
    return numerator / denominator


def expected_count(c: Contingency) -> float:
    """Expected ``a`` under independence: (drug_total * event_total) / total."""
    drug_total = c.a + c.b
    event_total = c.a + c.c
    total = c.a + c.b + c.c + c.d
    if total == 0:
        return 0.0
    return (drug_total * event_total) / total


def shrunken_log_score(c: Contingency, prior_strength: float = 0.5) -> float:
    """Simplified empirical-Bayes shrinkage of log(observed/expected).

    Low-count pairs produce unstable ROR/PRR. We shrink log(OE) toward 0 (no signal)
    with a weight that grows with the observed count ``a``::

        weight = a / (a + prior_strength)
        score  = weight * log(OE)

    This is a *prioritization* metric, deliberately named "simplified empirical Bayes
    shrinkage" rather than EBGM/MGPS to avoid implying regulatory equivalence
    (requirements §9.4).
    """
    exp = expected_count(c)
    if exp <= 0 or c.a <= 0:
        return 0.0
    oe = c.a / exp
    weight = c.a / (c.a + prior_strength)
    return weight * math.log(oe)


def disproportionality(c: Contingency) -> DisproportionalityResult:
    """Compute the full disproportionality bundle for one pair."""
    ror, lo, hi = reporting_odds_ratio(c)
    exp = expected_count(c)
    oe = (c.a / exp) if exp > 0 else float("nan")
    return DisproportionalityResult(
        ror=ror,
        ror_ci_lower=lo,
        ror_ci_upper=hi,
        prr=proportional_reporting_ratio(c),
        chi_square=chi_square_yates(c),
        expected_a=exp,
        oe_ratio=oe,
        shrunken_log_score=shrunken_log_score(c),
        continuity_correction=c.needs_correction,
    )


def is_signal(
    c: Contingency,
    result: DisproportionalityResult,
    *,
    minimum_reports: int,
    ror_lower_ci_threshold: float,
    prr_threshold: float,
    chi_square_threshold: float,
) -> bool:
    """Apply the configurable disproportionality flag rule (requirements §9.3).

    A flag is a prioritization signal, NOT a clinical conclusion.
    """
    return (
        c.a >= minimum_reports
        and result.ror_ci_lower > ror_lower_ci_threshold
        and result.prr >= prr_threshold
        and result.chi_square >= chi_square_threshold
    )


# --------------------------------------------------------------------------- #
# Time-series anomaly detection (requirements §9.5)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TrendResult:
    current_count: int
    trailing_baseline_mean: float
    percent_change: float
    z_score: float
    ewma: float
    poisson_anomaly_score: float  # 1 - P(X <= current | baseline rate); higher = rarer


def _poisson_sf(k: int, lam: float) -> float:
    """Survival function P(X >= k) for Poisson(lam), computed without scipy."""
    if lam <= 0:
        return 1.0 if k <= 0 else 0.0
    # P(X <= k-1) via stable iterative summation, then complement.
    cdf = 0.0
    term = math.exp(-lam)  # P(X=0)
    for i in range(0, k):
        cdf += term
        term *= lam / (i + 1)
    return max(0.0, min(1.0, 1.0 - cdf))


def trend_metrics(
    current_count: int,
    baseline_counts: list[int],
    *,
    ewma_alpha: float = 0.5,
) -> TrendResult:
    """Compare the current period to a trailing baseline of prior-quarter counts.

    ``baseline_counts`` is ordered oldest -> newest and excludes the current period.
    """
    n = len(baseline_counts)
    mean = sum(baseline_counts) / n if n else 0.0
    if n > 1:
        var = sum((x - mean) ** 2 for x in baseline_counts) / (n - 1)
        std = math.sqrt(var)
    else:
        std = 0.0

    percent_change = ((current_count - mean) / mean) if mean > 0 else float("nan")
    z = ((current_count - mean) / std) if std > 0 else float("nan")

    # EWMA over baseline then current observation.
    ewma = baseline_counts[0] if baseline_counts else float(current_count)
    for x in baseline_counts[1:]:
        ewma = ewma_alpha * x + (1 - ewma_alpha) * ewma
    ewma = ewma_alpha * current_count + (1 - ewma_alpha) * ewma

    poisson_score = _poisson_sf(current_count, mean) if mean > 0 else float("nan")

    return TrendResult(
        current_count=current_count,
        trailing_baseline_mean=mean,
        percent_change=percent_change,
        z_score=z,
        ewma=ewma,
        poisson_anomaly_score=poisson_score,
    )


# --------------------------------------------------------------------------- #
# Composite priority score (requirements §9.6)
# --------------------------------------------------------------------------- #
def _clip01(x: float) -> float:
    if x != x:  # NaN
        return 0.0
    return max(0.0, min(1.0, x))


def normalize_disproportionality(shrunken_log: float, cap: float = 3.0) -> float:
    """Map the shrinkage log-score onto [0, 1] (log(OE) of ~`cap` -> 1.0)."""
    return _clip01(shrunken_log / cap)


def normalize_trend(z_score: float, cap: float = 3.0) -> float:
    if z_score != z_score:  # NaN baseline
        return 0.0
    return _clip01(z_score / cap)


def priority_score(
    *,
    disproportionality_score: float,
    trend_anomaly_score: float,
    seriousness_score: float,
    literature_support_score: float,
    population_context_score: float,
    weights: dict[str, float],
) -> float:
    """Transparent, configurable composite. Components are never hidden (see §9.6)."""
    return (
        weights["disproportionality"] * _clip01(disproportionality_score)
        + weights["trend_anomaly"] * _clip01(trend_anomaly_score)
        + weights["seriousness"] * _clip01(seriousness_score)
        + weights["literature_support"] * _clip01(literature_support_score)
        + weights["population_context"] * _clip01(population_context_score)
    )


def priority_level(score: float, high: float, moderate: float) -> str:
    if score >= high:
        return "High"
    if score >= moderate:
        return "Moderate"
    return "Low"
