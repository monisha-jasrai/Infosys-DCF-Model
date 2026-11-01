"""FCFF DCF engine.

Notes on method:
- Valuation date is inside FY27, so FY27 is a stub period. Mid-year discounting.
- IFRS 16: D&A includes ROU depreciation, so new ROU assets are treated as reinvestment
  and lease liabilities are deducted as debt.
- SBC is not added back (it's a real cost, already in EBIT).
- Terminal value three ways: Gordon, exit EV/EBITDA, value driver
  NOPAT(1+g)(1-g/RONIC)/(WACC-g).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from .data import Inputs


@dataclass
class Drivers:
    """Forecast levers. Scenario shifts are additive on top of the base config."""
    revenue_growth: list[float]
    margin_start: float
    margin_end: float
    tax_rate: float
    da_pct: float
    capex_pct: float
    rou_pct: float
    nwc_pct: float
    terminal_growth: float
    exit_multiple: float
    ronic: float
    growth_shift: float = 0.0
    margin_shift: float = 0.0
    wacc_shift: float = 0.0

    @classmethod
    def from_config(cls, cfg: dict, scenario: str | None = None) -> "Drivers":
        f, t = cfg["forecast"], cfg["terminal"]
        d = cls(
            revenue_growth=list(f["revenue_growth"]),
            margin_start=f["ebit_margin_start"], margin_end=f["ebit_margin_end"],
            tax_rate=f["tax_rate"], da_pct=f["da_pct_revenue"], capex_pct=f["capex_pct_revenue"],
            rou_pct=f["rou_additions_pct_revenue"], nwc_pct=f["nwc_pct_revenue"],
            terminal_growth=t["growth"], exit_multiple=t["exit_ev_ebitda"], ronic=t["ronic"],
        )
        if scenario:
            s = cfg["scenarios"][scenario]
            d.growth_shift = s.get("growth_shift", 0.0)
            d.margin_shift = s.get("margin_shift", 0.0)
            d.wacc_shift = s.get("wacc_shift", 0.0)
            d.terminal_growth = s.get("terminal_growth", d.terminal_growth)
            d.exit_multiple = s.get("exit_ev_ebitda", d.exit_multiple)
        return d


@dataclass
class DCFResult:
    forecast: pd.DataFrame
    wacc: float
    stub_fraction: float
    ev: dict = field(default_factory=dict)
    equity: dict = field(default_factory=dict)
    per_share: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)
    headline: float = 0.0
    upside: float = 0.0


def fiscal_year_labels(base_label: str, n: int) -> list[str]:
    base = int(base_label.replace("FY", ""))
    return [f"FY{base + i}E" for i in range(1, n + 1)]


def stub_fraction(inputs: Inputs) -> float:
    """Share of FY27 still ahead of the valuation date (fiscal year ends 31 March)."""
    vd = inputs.valuation_date
    fy_end_year = vd.year + 1 if vd.month > 3 else vd.year
    import datetime as dt
    fy_end = dt.date(fy_end_year, 3, 31)
    return (fy_end - vd).days / 365.0


def discount_periods(stub: float, n: int) -> tuple[np.ndarray, np.ndarray, float]:
    """Mid-period discount times, cash-flow weights, and time to end of the final year."""
    t_mid = np.array([stub / 2] + [stub + (i - 1) + 0.5 for i in range(1, n)])
    weights = np.array([stub] + [1.0] * (n - 1))
    t_end = stub + (n - 1)
    return t_mid, weights, t_end


def build_forecast(inputs: Inputs, d: Drivers) -> pd.DataFrame:
    n = len(d.revenue_growth)
    years = fiscal_year_labels(inputs.historicals.columns[-1], n)
    rev_prev = inputs.base_revenue
    nwc_prev = rev_prev * d.nwc_pct
    rows = []
    for i in range(n):
        g = d.revenue_growth[i] + d.growth_shift
        rev = rev_prev * (1 + g)
        margin = d.margin_start + (d.margin_end - d.margin_start) * i / (n - 1) + d.margin_shift
        ebit = rev * margin
        tax = ebit * d.tax_rate
        nopat = ebit - tax
        da = rev * d.da_pct
        capex = rev * d.capex_pct
        rou = rev * d.rou_pct
        nwc = rev * d.nwc_pct
        d_nwc = nwc - nwc_prev
        fcff = nopat + da - capex - rou - d_nwc
        rows.append({
            "Revenue": rev, "Revenue growth": g, "EBIT margin": margin, "EBIT": ebit,
            "Taxes on EBIT": tax, "NOPAT": nopat, "D&A": da, "EBITDA": ebit + da,
            "Capex": capex, "ROU additions": rou, "Operating NWC": nwc, "Change in NWC": d_nwc,
            "FCFF": fcff, "Reinvestment rate": 1 - fcff / nopat,
        })
        rev_prev, nwc_prev = rev, nwc
    return pd.DataFrame(rows, index=years).T


def run_dcf(inputs: Inputs, base_wacc: float, d: Drivers) -> DCFResult:
    cfg = inputs.config
    wacc = base_wacc + d.wacc_shift
    g = d.terminal_growth
    if wacc - g <= 0.005:
        raise ValueError(f"WACC ({wacc:.2%}) must exceed terminal growth ({g:.2%}) by a margin")

    fc = build_forecast(inputs, d)
    n = fc.shape[1]
    stub = stub_fraction(inputs)
    t_mid, w, t_end = discount_periods(stub, n)
    df_mid = 1 / (1 + wacc) ** t_mid

    fcff = fc.loc["FCFF"].values
    fc.loc["Period weight"] = w
    fc.loc["Discount period (mid)"] = t_mid
    fc.loc["Discount factor"] = df_mid
    fc.loc["PV of FCFF"] = fcff * w * df_mid
    pv_explicit = float((fcff * w * df_mid).sum())

    fcff_n, nopat_n, ebitda_n = fcff[-1], fc.loc["NOPAT"].values[-1], fc.loc["EBITDA"].values[-1]
    # Terminal values. Gordon & value-driver value flows from mid-year N+1 onward -> discount
    # with the year-N mid-period factor. Exit multiple is a point value at the end of year N.
    tv_perp = fcff_n * (1 + g) / (wacc - g)
    tv_vd = nopat_n * (1 + g) * (1 - g / d.ronic) / (wacc - g)
    tv_exit = ebitda_n * d.exit_multiple
    pv_tv = {
        "perpetuity": tv_perp * df_mid[-1],
        "value_driver": tv_vd * df_mid[-1],
        "exit_multiple": tv_exit / (1 + wacc) ** t_end,
    }

    net_claims = inputs.cash_and_investments - inputs.lease_liabilities - inputs.borrowings - inputs.minority_interest
    ev = {k: pv_explicit + v for k, v in pv_tv.items()}
    equity = {k: v + net_claims for k, v in ev.items()}
    per_share = {k: v / inputs.shares for k, v in equity.items()}

    weights = cfg.get("headline_weights", {"perpetuity": 1.0})
    headline = sum(per_share[k] * wt for k, wt in weights.items()) / sum(weights.values())

    # Cross-checks: what the Gordon TV implies as a multiple, and what the exit multiple
    # implies as perpetual growth (TV_exit valued at end of N == Gordon on FCFF_{N+1}).
    tv_exit_mid = tv_exit * (1 + wacc) ** (t_mid[-1] - t_end)  # restate to the same timing basis
    implied_g = (tv_exit_mid * wacc - fcff_n) / (tv_exit_mid + fcff_n)
    checks = {
        "pv_explicit": pv_explicit,
        "tv_perpetuity": tv_perp, "tv_value_driver": tv_vd, "tv_exit": tv_exit,
        "pv_tv": pv_tv,
        "tv_share_of_ev_perpetuity": pv_tv["perpetuity"] / ev["perpetuity"],
        "implied_exit_multiple_from_gordon": tv_perp * (1 + wacc) ** (t_end - t_mid[-1]) / ebitda_n,
        "implied_growth_from_exit_multiple": implied_g,
        "terminal_reinvestment_rate": 1 - fcff_n / nopat_n,
        "implied_ronic_from_forecast": g / max(1 - fcff_n / nopat_n, 1e-9),
        "net_claims": net_claims,
    }
    return DCFResult(
        forecast=fc, wacc=wacc, stub_fraction=stub, ev=ev, equity=equity, per_share=per_share,
        checks=checks, headline=headline, upside=headline / inputs.price - 1,
    )


# ---------------------------------------------------------------------------
# Vectorised engine for Monte Carlo (same maths as run_dcf, thousands of paths at once)
# ---------------------------------------------------------------------------
def value_per_share_vectorized(inputs: Inputs, d: Drivers, growth_shift, margin_shift, wacc,
                               terminal_growth, exit_multiple, method: str = "blend") -> np.ndarray:
    growth_shift = np.asarray(growth_shift, float)
    margin_shift = np.asarray(margin_shift, float)
    wacc = np.asarray(wacc, float)
    g = np.asarray(terminal_growth, float)
    exit_multiple = np.asarray(exit_multiple, float)

    n = len(d.revenue_growth)
    stub = stub_fraction(inputs)
    t_mid, w, t_end = discount_periods(stub, n)
    rev_prev = np.full_like(wacc, inputs.base_revenue)
    nwc_prev = rev_prev * d.nwc_pct
    pv = np.zeros_like(wacc)
    for i in range(n):
        rev = rev_prev * (1 + d.revenue_growth[i] + growth_shift)
        margin = d.margin_start + (d.margin_end - d.margin_start) * i / (n - 1) + margin_shift
        ebit = rev * margin
        nopat = ebit * (1 - d.tax_rate)
        da = rev * d.da_pct
        nwc = rev * d.nwc_pct
        fcff = nopat + da - rev * (d.capex_pct + d.rou_pct) - (nwc - nwc_prev)
        pv += fcff * w[i] / (1 + wacc) ** t_mid[i]
        rev_prev, nwc_prev = rev, nwc
    ebitda_n = ebit + da
    pv_perp = fcff * (1 + g) / (wacc - g) / (1 + wacc) ** t_mid[-1]
    pv_vd = nopat * (1 + g) * (1 - g / d.ronic) / (wacc - g) / (1 + wacc) ** t_mid[-1]
    pv_exit = ebitda_n * exit_multiple / (1 + wacc) ** t_end
    net_claims = inputs.cash_and_investments - inputs.lease_liabilities - inputs.borrowings - inputs.minority_interest
    pvs = {"perpetuity": pv_perp, "value_driver": pv_vd, "exit_multiple": pv_exit}
    if method in pvs:
        pv_tv = pvs[method]
    else:
        wts = inputs.config.get("headline_weights", {"perpetuity": 1.0})
        tot = sum(wts.values())
        pv_tv = sum(wt * pvs[k] for k, wt in wts.items()) / tot
    return (pv + pv_tv + net_claims) / inputs.shares


# ---------------------------------------------------------------------------
# Reverse DCF: what is the market pricing in?
# ---------------------------------------------------------------------------
def reverse_dcf(inputs: Inputs, base_wacc: float, d: Drivers) -> dict:
    """Solve for the uniform growth shift (and the resulting 10y revenue CAGR) at which the
    value-driver DCF equals today's share price. Also solves the implied WACC.
    Uses the value-driver method because it doesn't depend on a market multiple."""
    def price_at_shift(shift):
        dd = Drivers(**{**d.__dict__, "growth_shift": shift})
        return run_dcf(inputs, base_wacc, dd).per_share["value_driver"] - inputs.price

    shift = brentq(price_at_shift, -0.15, 0.25, xtol=1e-7)
    dd = Drivers(**{**d.__dict__, "growth_shift": shift})
    fc = build_forecast(inputs, dd)
    n = fc.shape[1]
    implied_cagr = (fc.loc["Revenue"].iloc[-1] / inputs.base_revenue) ** (1 / n) - 1
    base_cagr = (build_forecast(inputs, d).loc["Revenue"].iloc[-1] / inputs.base_revenue) ** (1 / n) - 1

    def price_at_wacc(wc):
        return run_dcf(inputs, wc, d).per_share["value_driver"] - inputs.price

    implied_wacc = brentq(price_at_wacc, d.terminal_growth + 0.006, 0.30, xtol=1e-7)
    return {
        "implied_growth_shift": shift,
        "implied_revenue_cagr": implied_cagr,
        "base_revenue_cagr": base_cagr,
        "implied_wacc": implied_wacc,
    }
