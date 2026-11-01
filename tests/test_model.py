"""Unit tests: accounting identities, engine consistency, and finance sanity checks."""
import numpy as np
import pytest

from dcf.analysis import comps, monte_carlo, run_scenarios
from dcf.data import load_inputs
from dcf.model import Drivers, build_forecast, reverse_dcf, run_dcf, stub_fraction, value_per_share_vectorized
from dcf.wacc import blume_adjust, compute_wacc, regression_beta


@pytest.fixture(scope="module")
def inp():
    return load_inputs()


@pytest.fixture(scope="module")
def wacc(inp):
    return compute_wacc(inp.config["wacc"], inp.market_cap, inp.lease_liabilities).wacc


@pytest.fixture(scope="module")
def base(inp):
    return Drivers.from_config(inp.config)


# ---------------- data ----------------
def test_inr_translation_ties_to_reported_revenue(inp):
    h = inp.historicals
    assert h.loc["Revenue", "FY26"] == 178650
    usd = inp.snapshot["income_statement_usd"]["revenue"][-1]
    assert h.loc["Implied avg USD/INR", "FY26"] == pytest.approx(178650 / (usd / 10))


def test_historical_margins_plausible(inp):
    m = inp.historicals.loc["EBIT margin"]
    assert m.between(0.19, 0.24).all()


# ---------------- WACC ----------------
def test_blume():
    assert blume_adjust(1.0) == pytest.approx(1.0)
    assert blume_adjust(0.85) == pytest.approx(0.8995)


def test_wacc_build_up(inp):
    w = compute_wacc(inp.config["wacc"], 1000.0, 0.0)
    c = inp.config["wacc"]
    assert w.risk_free == pytest.approx(c["gsec_10y"] - c["india_default_spread"])
    ke = w.risk_free + blume_adjust(c["beta"]["raw"]) * c["mature_market_erp"] + c["lambda"] * c["country_risk_premium"]
    assert w.cost_of_equity == pytest.approx(ke)
    assert w.wacc == pytest.approx(ke)  # all-equity


def test_regression_beta_recovers_true_beta():
    rng = np.random.default_rng(0)
    mkt = rng.normal(0.002, 0.02, 500)
    stock = 0.001 + 0.8 * mkt + rng.normal(0, 0.01, 500)
    reg = regression_beta(stock, mkt)
    assert reg["beta"] == pytest.approx(0.8, abs=0.06)
    assert 0 < reg["r_squared"] < 1


# ---------------- DCF mechanics ----------------
def test_stub_fraction(inp):
    assert stub_fraction(inp) == pytest.approx(154 / 365)


def test_fcff_identity(inp, base):
    fc = build_forecast(inp, base)
    recon = fc.loc["NOPAT"] + fc.loc["D&A"] - fc.loc["Capex"] - fc.loc["ROU additions"] - fc.loc["Change in NWC"]
    assert np.allclose(recon, fc.loc["FCFF"])


def test_vectorized_engine_matches_detailed(inp, wacc, base):
    r = run_dcf(inp, wacc, base)
    for method, key in (("perpetuity", "perpetuity"), ("value_driver", "value_driver"), ("exit_multiple", "exit_multiple")):
        v = value_per_share_vectorized(inp, base, [0.0], [0.0], [wacc], [base.terminal_growth],
                                       [base.exit_multiple], method=method)[0]
        assert v == pytest.approx(r.per_share[key], rel=1e-10)


def test_terminal_cross_check_round_trip(inp, wacc, base):
    """Using the Gordon-implied exit multiple must give back the Gordon value."""
    r = run_dcf(inp, wacc, base)
    d2 = Drivers(**{**base.__dict__, "exit_multiple": r.checks["implied_exit_multiple_from_gordon"]})
    r2 = run_dcf(inp, wacc, d2)
    assert r2.per_share["exit_multiple"] == pytest.approx(r.per_share["perpetuity"], rel=1e-9)
    assert r2.checks["implied_growth_from_exit_multiple"] == pytest.approx(base.terminal_growth, abs=1e-9)


def test_value_driver_equals_gordon_when_ronic_matches_reinvestment(inp, wacc, base):
    r = run_dcf(inp, wacc, base)
    d2 = Drivers(**{**base.__dict__, "ronic": r.checks["implied_ronic_from_forecast"]})
    r2 = run_dcf(inp, wacc, d2)
    assert r2.per_share["value_driver"] == pytest.approx(r.per_share["perpetuity"], rel=0.02)


def test_wacc_below_growth_rejected(inp, base):
    with pytest.raises(ValueError):
        run_dcf(inp, base.terminal_growth, base)


def test_value_monotonic_in_wacc(inp, wacc, base):
    lo = run_dcf(inp, wacc - 0.01, base).headline
    hi = run_dcf(inp, wacc + 0.01, base).headline
    assert lo > hi


# ---------------- analytics ----------------
def test_scenarios_ordered(inp, wacc):
    s = run_scenarios(inp, wacc)["Value / share (Blended)"]
    assert s["bear"] < s["base"] < s["bull"]


def test_reverse_dcf_hits_market_price(inp, wacc, base):
    rv = reverse_dcf(inp, wacc, base)
    d2 = Drivers(**{**base.__dict__, "growth_shift": rv["implied_growth_shift"]})
    assert run_dcf(inp, wacc, d2).per_share["value_driver"] == pytest.approx(inp.price, abs=0.01)
    assert run_dcf(inp, rv["implied_wacc"], base).per_share["value_driver"] == pytest.approx(inp.price, abs=0.01)


def test_monte_carlo_reproducible_and_centred(inp, wacc, base):
    inp2 = inp.copy()
    inp2.config["monte_carlo"]["simulations"] = 3000
    a = monte_carlo(inp2, wacc, base)
    b = monte_carlo(inp2, wacc, base)
    assert np.array_equal(a.values, b.values)
    assert a.stats["p50"] == pytest.approx(run_dcf(inp, wacc, base).headline, rel=0.05)
    assert a.stats["p10"] < a.stats["p50"] < a.stats["p90"]


def test_comps_implied_prices(inp):
    c = comps(inp)
    pe = c["P/E"]["implied_price"]
    assert pe["low"] <= pe["median"] <= pe["high"]
    assert c["infy_pe"] == pytest.approx(inp.price / c["ttm_eps"])
