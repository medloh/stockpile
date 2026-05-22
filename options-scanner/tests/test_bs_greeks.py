"""Tests for Black-Scholes helpers in chain.py.

These functions underpin every IV-excess rank, every delta filter, and
the entire GEX chart, so it's worth pinning their behavior against
closed-form expected values. If `_bs_delta`, `_bs_gamma`, `_norm_cdf`,
or `_norm_pdf` regress, the symptoms downstream are subtle (mis-ranked
picks, slightly-wrong GEX magnitudes) rather than crashes.

Reference values:
  - N(0)        = 0.5            (standard normal CDF at zero)
  - N(1.96)     ≈ 0.9750021       (97.5th percentile)
  - φ(0)        = 1/√(2π) ≈ 0.3989422804
  - φ(1) = φ(-1) ≈ 0.2419707245   (symmetry)
"""

import math

import pytest

from options_scanner.chain import _bs_delta, _bs_gamma, _norm_cdf, _norm_pdf


# ── Standard normal helpers ──────────────────────────────────────────────────

class TestNormCdf:
    def test_zero_is_half(self):
        assert abs(_norm_cdf(0.0) - 0.5) < 1e-12

    def test_far_negative_is_zero(self):
        assert _norm_cdf(-10.0) < 1e-15

    def test_far_positive_is_one(self):
        assert abs(_norm_cdf(10.0) - 1.0) < 1e-15

    def test_975th_percentile(self):
        assert abs(_norm_cdf(1.96) - 0.9750021) < 1e-6

    def test_symmetry_around_zero(self):
        """N(-x) + N(x) = 1 for all x — sanity for the CDF."""
        for x in [0.1, 0.5, 1.0, 1.5, 2.0]:
            assert abs(_norm_cdf(-x) + _norm_cdf(x) - 1.0) < 1e-12


class TestNormPdf:
    def test_zero_is_one_over_sqrt_two_pi(self):
        expected = 1.0 / math.sqrt(2 * math.pi)
        assert abs(_norm_pdf(0.0) - expected) < 1e-12

    def test_known_value_at_one(self):
        assert abs(_norm_pdf(1.0) - 0.2419707245) < 1e-9

    def test_symmetry(self):
        """φ(x) = φ(-x) — pdf is even."""
        for x in [0.1, 0.5, 1.0, 2.0]:
            assert abs(_norm_pdf(x) - _norm_pdf(-x)) < 1e-15

    def test_far_tails_approach_zero(self):
        assert _norm_pdf(10.0) < 1e-20
        assert _norm_pdf(-10.0) < 1e-20


# ── BS delta ─────────────────────────────────────────────────────────────────

class TestBsDelta:
    """Black-Scholes delta with closed-form expected values."""

    def test_atm_call_at_zero_rate_is_about_half(self):
        """ATM call with r=0 and σ=0.20, T=1: d1 = 0.5σ√T = 0.1, so
        delta = N(0.1) ≈ 0.5398 — slightly above 0.5."""
        d = _bs_delta(S=100, K=100, T=1.0, r=0.0, sigma=0.20,
                      opt_type="call")
        assert abs(d - _norm_cdf(0.1)) < 1e-12
        assert 0.50 < d < 0.60

    def test_atm_put_at_zero_rate_is_about_negative_half(self):
        """Put-call parity for delta: Δ_put = Δ_call − 1."""
        call = _bs_delta(S=100, K=100, T=1.0, r=0.0, sigma=0.20,
                         opt_type="call")
        put  = _bs_delta(S=100, K=100, T=1.0, r=0.0, sigma=0.20,
                         opt_type="put")
        assert abs((call - 1.0) - put) < 1e-12
        # call ≈ N(0.1) ≈ 0.5398, so put = call − 1 ≈ −0.4602
        assert -0.40 > put > -0.50

    def test_deep_itm_call_delta_approaches_one(self):
        d = _bs_delta(S=200, K=100, T=0.25, r=0.045, sigma=0.20,
                      opt_type="call")
        assert d > 0.999

    def test_deep_otm_call_delta_approaches_zero(self):
        d = _bs_delta(S=50, K=100, T=0.25, r=0.045, sigma=0.20,
                      opt_type="call")
        assert d < 0.01

    def test_deep_itm_put_delta_approaches_negative_one(self):
        d = _bs_delta(S=50, K=100, T=0.25, r=0.045, sigma=0.20,
                      opt_type="put")
        assert d < -0.999

    def test_deep_otm_put_delta_approaches_zero(self):
        d = _bs_delta(S=200, K=100, T=0.25, r=0.045, sigma=0.20,
                      opt_type="put")
        assert d > -0.01

    def test_call_put_parity_holds_across_range(self):
        """Δ_call − Δ_put = 1 for European options (with no dividend),
        regardless of moneyness or DTE. Verifies sign and offset
        conventions are consistent."""
        for S in [80, 100, 120]:
            for T in [0.05, 0.5, 2.0]:
                call = _bs_delta(S=S, K=100, T=T, r=0.045, sigma=0.30,
                                 opt_type="call")
                put  = _bs_delta(S=S, K=100, T=T, r=0.045, sigma=0.30,
                                 opt_type="put")
                assert abs((call - put) - 1.0) < 1e-12

    def test_expired_itm_call_is_one(self):
        """T=0 fallback: delta is intrinsic-payoff indicator."""
        assert _bs_delta(S=150, K=100, T=0.0, r=0.045, sigma=0.20,
                         opt_type="call") == 1.0

    def test_expired_otm_call_is_zero(self):
        assert _bs_delta(S=50, K=100, T=0.0, r=0.045, sigma=0.20,
                         opt_type="call") == 0.0

    def test_expired_itm_put_is_negative_one(self):
        assert _bs_delta(S=50, K=100, T=0.0, r=0.045, sigma=0.20,
                         opt_type="put") == -1.0

    def test_expired_otm_put_is_zero(self):
        assert _bs_delta(S=150, K=100, T=0.0, r=0.045, sigma=0.20,
                         opt_type="put") == 0.0

    def test_sub_threshold_sigma_uses_intrinsic_fallback(self):
        """σ < 0.001 also triggers the intrinsic fallback (would
        otherwise produce inf/NaN from division)."""
        assert _bs_delta(S=150, K=100, T=0.5, r=0.045, sigma=0.0,
                         opt_type="call") == 1.0


# ── BS gamma ─────────────────────────────────────────────────────────────────

class TestBsGamma:
    """Black-Scholes gamma — same for calls and puts; peaks ATM and
    decays toward zero on both sides."""

    def test_gamma_is_positive_atm(self):
        g = _bs_gamma(S=100, K=100, T=0.25, r=0.045, sigma=0.20)
        assert g > 0

    def test_gamma_peaks_near_the_money(self):
        """Gamma should be larger ATM than at strikes 20% away."""
        atm  = _bs_gamma(S=100, K=100, T=0.25, r=0.045, sigma=0.20)
        wing = _bs_gamma(S=120, K=100, T=0.25, r=0.045, sigma=0.20)
        assert atm > wing
        wing_below = _bs_gamma(S=80, K=100, T=0.25, r=0.045, sigma=0.20)
        assert atm > wing_below

    def test_gamma_approaches_zero_for_deep_itm(self):
        g = _bs_gamma(S=200, K=100, T=0.25, r=0.045, sigma=0.20)
        assert g < 1e-6

    def test_gamma_approaches_zero_for_deep_otm(self):
        g = _bs_gamma(S=50, K=100, T=0.25, r=0.045, sigma=0.20)
        assert g < 1e-6

    def test_expired_option_has_zero_gamma(self):
        """T=0 fallback: no time, no convexity."""
        assert _bs_gamma(S=100, K=100, T=0.0, r=0.045, sigma=0.20) == 0.0

    def test_sub_threshold_sigma_returns_zero(self):
        assert _bs_gamma(S=100, K=100, T=0.5, r=0.045, sigma=0.0) == 0.0

    def test_nonpositive_spot_returns_zero(self):
        """Defensive: log(S/K) blows up at S=0; guard returns 0."""
        assert _bs_gamma(S=0.0, K=100, T=0.5, r=0.045, sigma=0.20) == 0.0

    def test_known_atm_gamma_value(self):
        """Closed form: at S=K=100, T=1, r=0, σ=0.20 →
        d1 = 0.5σ√T = 0.1, gamma = φ(d1) / (S·σ·√T)
                              = φ(0.1) / (100 · 0.20 · 1)
                              ≈ 0.3969525 / 20 ≈ 0.01984763.
        """
        g = _bs_gamma(S=100, K=100, T=1.0, r=0.0, sigma=0.20)
        d1 = 0.1
        expected = _norm_pdf(d1) / (100.0 * 0.20 * 1.0)
        assert abs(g - expected) < 1e-12
