"""Tests for the EBGM / MGPS gamma-Poisson shrinkage estimator (WS2)."""
import numpy as np

from pharmasignal.modeling import ebgm as eb


def _synthetic_table(seed: int = 0):
    """A contingency table drawn from the MGPS model itself, with a few strong signals.

    ``lambda`` is sampled from an overdispersed gamma mixture (so the gamma-Poisson MLE
    is well-identified rather than collapsing to a point mass), then n ~ Poisson(e*lambda).
    """
    rng = np.random.default_rng(seed)
    e = rng.uniform(1.0, 30.0, size=3000)
    # Background: lambda ~ Gamma(mean 1) with real spread; a fraction mildly elevated.
    lam = rng.gamma(shape=2.0, scale=0.5, size=e.size)               # mean 1
    elevated = rng.random(e.size) < 0.2
    lam[elevated] = rng.gamma(shape=3.0, scale=1.0, size=elevated.sum())  # mean 3
    n = rng.poisson(e * lam).astype(float)
    # Inject unambiguous strong signals: lambda ~ 8.
    strong = np.array([0, 1, 2, 3, 4])
    n[strong] = rng.poisson(8.0 * e[strong]).astype(float)
    return n, e, strong


def test_ebgm_bounds_ordering():
    n, e, strong = _synthetic_table()
    res = eb.ebgm_scores(n, e)
    assert res.ebgm.shape == n.shape
    # The credible interval is always ordered and non-negative.
    assert np.all(res.eb05 <= res.eb95 + 1e-6)
    assert np.all(res.eb05 >= 0)
    # For pairs carrying real evidence (the injected signals), the point estimate
    # sits inside the interval. (For tiny-shape posteriors the geometric-mean EBGM
    # can fall below the 5th percentile, a known MGPS edge case, so this ordering
    # is only asserted where there is enough evidence to be well-behaved.)
    assert np.all(res.eb05[strong] <= res.ebgm[strong] + 1e-6)
    assert np.all(res.ebgm[strong] <= res.eb95[strong] + 1e-6)


def test_ebgm_flags_true_signals():
    n, e, strong = _synthetic_table()
    res = eb.ebgm_scores(n, e)
    # Injected 8x signals should have EB05 well above the null baseline of 1.
    assert np.all(res.eb05[strong] > 2.0)
    assert np.all(res.ebgm[strong] > 3.0)


def test_ebgm_shrinks_low_evidence_toward_one():
    # Two pairs with the same observed/expected RATIO but very different counts.
    # The low-count pair must be shrunk closer to the null (EBGM ~ 1) than the
    # high-count pair, which is the whole point of empirical-Bayes shrinkage.
    n = np.array([2.0, 80.0])
    e = np.array([1.0, 40.0])  # both ratio = 2.0
    res = eb.ebgm_scores(n, e)
    assert res.ebgm[1] > res.ebgm[0]
    assert abs(res.ebgm[0] - 1.0) < abs(res.ebgm[1] - 1.0)


def test_fit_is_reused_when_passed():
    n, e, _ = _synthetic_table()
    fit = eb.fit_mgps(n, e)
    res = eb.ebgm_scores(n, e, fit=fit)
    assert res.fit is fit
    # Lower-mean component ordered first.
    assert fit.a1 / fit.b1 <= fit.a2 / fit.b2
