"""Empirical-Bayes Geometric Mean (EBGM / MGPS) — regulatory-grade shrinkage.

This is the estimator the full-FAERS upgrade plan (WS2 / `future_enhancements.md` #8)
calls for: a two-component gamma-Poisson mixture (DuMouchel 1999, "Multi-item Gamma
Poisson Shrinker") fit *once* over the whole drug x event contingency table, emitting
``ebgm`` and the 5th-percentile ``eb05`` per pair.

Why it matters at whole-database scale: raw ROR is dominated by the millions of
small-count pairs, where a single coincidental co-report yields an enormous ratio.
EBGM shrinks each pair toward the no-association baseline (EBGM = 1) by an amount that
depends on how much evidence the pair actually carries, using the empirical marginal
distribution of *all* pairs as the prior. ``eb05`` (lower 5% credible bound) is the
conventional signalling threshold — a pair with ``eb05 >= 2`` is a robust signal.

Like every metric in :mod:`signal_scores`, these are hypothesis-generating
prioritization statistics, not measures of causality or clinical risk.

Model
-----
For pair *i* with observed count ``n_i`` and expected count ``e_i`` (under
independence)::

    lambda_i ~ pi * Gamma(a1, b1) + (1 - pi) * Gamma(a2, b2)     (rate parametrization)
    n_i | lambda_i ~ Poisson(e_i * lambda_i)

The marginal of ``n_i`` is a mixture of two negative binomials. The five
hyperparameters ``theta = (a1, b1, a2, b2, pi)`` are fit by maximum likelihood. The
posterior of ``lambda_i`` is again a two-component gamma mixture, from which EBGM
(geometric-mean posterior) and EB05 (5th percentile) are read off.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.special import digamma, gammaln
from scipy.stats import gamma as gamma_dist

_LN2 = np.log(2.0)

# DuMouchel's default starting hyperparameters (a1, b1, a2, b2, pi).
_INIT_THETA = (0.2, 0.1, 2.0, 4.0, 0.3333)


@dataclass(frozen=True)
class MGPSFit:
    """Fitted hyperparameters of the two-component gamma-Poisson mixture."""

    a1: float
    b1: float
    a2: float
    b2: float
    pi: float
    log_likelihood: float
    n_pairs: int


def _nb_log_pmf(n: np.ndarray, alpha: float, beta: float, e: np.ndarray) -> np.ndarray:
    """log P(N=n) for the gamma-Poisson marginal Gamma(alpha, rate=beta) x Poisson(e*.).

    NB with r = alpha, p = beta / (beta + e):
        log pmf = lgamma(alpha+n) - lgamma(alpha) - lgamma(n+1)
                  + alpha*log(p) + n*log(1-p)
    """
    p = beta / (beta + e)
    return (
        gammaln(alpha + n)
        - gammaln(alpha)
        - gammaln(n + 1.0)
        + alpha * np.log(p)
        + n * np.log1p(-p)
    )


# Bounds on log(shape)/log(rate). Capping shapes/rates well below the degenerate
# regime (~1e7+) stops the MLE collapsing the prior to a two-point mass on clean data,
# which would invert EBGM vs EB05; MGPS hyperparameters are realistically O(1e-2..1e2).
_LOG_PARAM_BOUNDS = (np.log(1e-3), np.log(1e3))
_LOGIT_PI_BOUNDS = (-6.0, 6.0)


def _unpack(raw: np.ndarray) -> tuple[float, float, float, float, float]:
    """Map the unconstrained optimizer vector to constrained hyperparameters.

    a1,b1,a2,b2 are exp() (positive); pi is a logistic (0,1).
    """
    a1, b1, a2, b2 = np.exp(raw[:4])
    pi = 1.0 / (1.0 + np.exp(-raw[4]))
    return float(a1), float(b1), float(a2), float(b2), float(pi)


def _neg_log_likelihood(raw: np.ndarray, n: np.ndarray, e: np.ndarray) -> float:
    a1, b1, a2, b2, pi = _unpack(raw)
    log_f1 = _nb_log_pmf(n, a1, b1, e)
    log_f2 = _nb_log_pmf(n, a2, b2, e)
    # log-sum-exp of the two weighted components, elementwise.
    m = np.maximum(log_f1, log_f2)
    mix = m + np.log(pi * np.exp(log_f1 - m) + (1.0 - pi) * np.exp(log_f2 - m))
    return -float(np.sum(mix))


def fit_mgps(
    n: np.ndarray,
    e: np.ndarray,
    *,
    max_fit_pairs: int = 200_000,
    random_state: int = 0,
) -> MGPSFit:
    """Fit the two-component gamma-Poisson mixture by maximum likelihood.

    ``n`` observed counts, ``e`` expected counts (same length, both >= 0). For very
    large tables the hyperparameters are estimated on a random subsample of size
    ``max_fit_pairs`` (the MLE of five global parameters is stable well below the full
    matrix), then applied to every pair in :func:`ebgm_scores`.
    """
    n = np.asarray(n, dtype=float)
    e = np.asarray(e, dtype=float)
    if n.shape != e.shape:
        raise ValueError("n and e must have the same shape")
    if n.size == 0:
        raise ValueError("cannot fit MGPS on an empty table")

    fit_n, fit_e = n, e
    if n.size > max_fit_pairs:
        rng = np.random.default_rng(random_state)
        idx = rng.choice(n.size, size=max_fit_pairs, replace=False)
        fit_n, fit_e = n[idx], e[idx]

    # Guard expected counts away from exactly 0 (undefined NB probability).
    fit_e = np.clip(fit_e, 1e-9, None)

    x0 = np.array(
        [
            np.log(_INIT_THETA[0]),
            np.log(_INIT_THETA[1]),
            np.log(_INIT_THETA[2]),
            np.log(_INIT_THETA[3]),
            np.log(_INIT_THETA[4] / (1 - _INIT_THETA[4])),
        ]
    )
    bounds = [_LOG_PARAM_BOUNDS] * 4 + [_LOGIT_PI_BOUNDS]
    res = minimize(
        _neg_log_likelihood,
        x0,
        args=(fit_n, fit_e),
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": 5000, "ftol": 1e-9},
    )
    a1, b1, a2, b2, pi = _unpack(res.x)
    # Order components so component 1 is the lower-mean ("background") one.
    if a1 / b1 > a2 / b2:
        a1, b1, a2, b2, pi = a2, b2, a1, b1, 1.0 - pi
    return MGPSFit(
        a1=a1, b1=b1, a2=a2, b2=b2, pi=pi,
        log_likelihood=-float(res.fun), n_pairs=int(n.size),
    )


def _posterior_weights(
    n: np.ndarray, e: np.ndarray, fit: MGPSFit
) -> tuple[np.ndarray, np.ndarray]:
    """Posterior probability each pair belongs to component 1 vs 2 (Qn, 1-Qn)."""
    log_f1 = _nb_log_pmf(n, fit.a1, fit.b1, e) + np.log(fit.pi)
    log_f2 = _nb_log_pmf(n, fit.a2, fit.b2, e) + np.log(1.0 - fit.pi)
    m = np.maximum(log_f1, log_f2)
    w1 = np.exp(log_f1 - m)
    w2 = np.exp(log_f2 - m)
    denom = w1 + w2
    return w1 / denom, w2 / denom


@dataclass(frozen=True)
class EBGMResult:
    ebgm: np.ndarray
    eb05: np.ndarray
    eb95: np.ndarray
    fit: MGPSFit


def ebgm_scores(
    n: np.ndarray,
    e: np.ndarray,
    *,
    fit: MGPSFit | None = None,
    lower_pct: float = 0.05,
    upper_pct: float = 0.95,
) -> EBGMResult:
    """Compute EBGM and its credible bounds for every (n, e) pair.

    EBGM = 2 ** E[log2(lambda) | n], the posterior geometric mean. EB05/EB95 are the
    ``lower_pct``/``upper_pct`` quantiles of the posterior gamma mixture, found by
    vectorized bisection on the mixture CDF.
    """
    n = np.asarray(n, dtype=float)
    e = np.clip(np.asarray(e, dtype=float), 1e-9, None)
    if fit is None:
        fit = fit_mgps(n, e)

    q1, q2 = _posterior_weights(n, e, fit)

    # Posterior gamma params per component: shape = a_k + n, rate = b_k + e.
    shape1, rate1 = fit.a1 + n, fit.b1 + e
    shape2, rate2 = fit.a2 + n, fit.b2 + e

    # EBGM = 2 ** ( sum_k Q_k * (digamma(shape_k) - ln(rate_k)) / ln2 )
    e_log2 = (
        q1 * (digamma(shape1) - np.log(rate1))
        + q2 * (digamma(shape2) - np.log(rate2))
    ) / _LN2
    ebgm = np.power(2.0, e_log2)

    eb_low = _mixture_quantile(lower_pct, q1, shape1, rate1, q2, shape2, rate2)
    eb_high = _mixture_quantile(upper_pct, q1, shape1, rate1, q2, shape2, rate2)
    return EBGMResult(ebgm=ebgm, eb05=eb_low, eb95=eb_high, fit=fit)


def _mixture_quantile(
    pct: float,
    q1: np.ndarray, shape1: np.ndarray, rate1: np.ndarray,
    q2: np.ndarray, shape2: np.ndarray, rate2: np.ndarray,
    *,
    iters: int = 60,
) -> np.ndarray:
    """Vectorized quantile of the two-component posterior gamma mixture.

    Solves CDF(x) = pct per pair via bisection. The mixture CDF is monotone in x, so
    bisection converges reliably for every element simultaneously.
    """
    def mix_cdf(x: np.ndarray) -> np.ndarray:
        # scipy gamma uses scale = 1/rate.
        c1 = gamma_dist.cdf(x, a=shape1, scale=1.0 / rate1)
        c2 = gamma_dist.cdf(x, a=shape2, scale=1.0 / rate2)
        return q1 * c1 + q2 * c2

    lo = np.zeros_like(shape1)
    # Upper bracket: generous multiple of each component's posterior mean.
    hi = 10.0 * np.maximum(shape1 / rate1, shape2 / rate2) + 10.0
    # Expand hi until it brackets the target (rare for extreme upper quantiles).
    for _ in range(40):
        need = mix_cdf(hi) < pct
        if not np.any(need):
            break
        hi = np.where(need, hi * 2.0, hi)

    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        go_right = mix_cdf(mid) < pct
        lo = np.where(go_right, mid, lo)
        hi = np.where(go_right, hi, mid)
    return 0.5 * (lo + hi)
