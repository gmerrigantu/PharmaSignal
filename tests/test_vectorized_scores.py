"""The vectorized scorer must agree with the scalar formulas to floating point (WS2)."""
import numpy as np
import pytest

from pharmasignal.modeling import signal_scores as ss


# A mix of clean tables and tables with zero cells (continuity-correction path).
CELLS = [
    (20, 80, 10, 890),
    (5, 0, 10, 100),       # zero b -> needs correction
    (100, 1000, 200, 1_000_000),
    (1, 50, 3, 9000),
    (0, 40, 7, 8000),      # zero a
]


def test_vectorized_matches_scalar():
    a = np.array([c[0] for c in CELLS], float)
    b = np.array([c[1] for c in CELLS], float)
    c = np.array([c[2] for c in CELLS], float)
    d = np.array([c[3] for c in CELLS], float)
    vec = ss.disproportionality_frame(a, b, c, d)

    for i, (ai, bi, ci, di) in enumerate(CELLS):
        cont = ss.Contingency(a=ai, b=bi, c=ci, d=di)
        scalar = ss.disproportionality(cont)
        assert vec["ror"][i] == pytest.approx(scalar.ror, rel=1e-9)
        assert vec["ror_ci_lower"][i] == pytest.approx(scalar.ror_ci_lower, rel=1e-9)
        assert vec["ror_ci_upper"][i] == pytest.approx(scalar.ror_ci_upper, rel=1e-9)
        assert vec["prr"][i] == pytest.approx(scalar.prr, rel=1e-9)
        assert vec["chi_square"][i] == pytest.approx(scalar.chi_square, rel=1e-9)
        assert vec["expected_count"][i] == pytest.approx(scalar.expected_a, rel=1e-9)
        assert vec["bayesian_shrunken_score"][i] == pytest.approx(
            scalar.shrunken_log_score, rel=1e-9)
        assert bool(vec["continuity_correction"][i]) == scalar.continuity_correction
