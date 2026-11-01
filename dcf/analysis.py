"""Scenario analysis, sensitivity grids, tornado chart data, Monte Carlo and comps."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .data import Inputs
from .model import Drivers, run_dcf, value_per_share_vectorized


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
def run_scenarios(inputs: Inputs, base_wacc: float) -> pd.DataFrame:
    rows = []
    for key, sc in inputs.config["scenarios"].items():
        d = Drivers.from_config(inputs.config, key)
        r = run_dcf(inputs, base_wacc, d)
        fc = r.forecast
        n = fc.shape[1]
        rows.append({
            "Scenario": sc["label"],
            "key": key,
            "Revenue CAGR (10y)": (fc.loc["Revenue"].iloc[-1] / inputs.base_revenue) ** (1 / n) - 1,
            "Avg EBIT margin": fc.loc["EBIT margin"].mean(),
            "WACC": r.wacc,
            "Terminal growth": d.terminal_growth,
            "Exit EV/EBITDA": d.exit_multiple,
            "Value / share (Gordon)": r.per_share["perpetuity"],
            "Value / share (Exit)": r.per_share["exit_multiple"],
            "Value / share (Blended)": r.headline,
            "Upside vs price": r.upside,
        })
    return pd.DataFrame(rows).set_index("key")


# ---------------------------------------------------------------------------
# Sensitivities
# ---------------------------------------------------------------------------
def grid(inputs: Inputs, base_wacc: float, d: Drivers, row_param: str, row_vals, col_param: str,
         col_vals, method: str = "perpetuity") -> pd.DataFrame:
    """Two-way sensitivity of value/share. Params: wacc, terminal_growth, exit_multiple,
    margin_shift, growth_shift."""
    out = np.zeros((len(row_vals), len(col_vals)))
    for i, rv in enumerate(row_vals):
        for j, cv in enumerate(col_vals):
            dd = Drivers(**d.__dict__)
            w = base_wacc
            for p, v in ((row_param, rv), (col_param, cv)):
                if p == "wacc":
                    w = v
                else:
                    setattr(dd, p, v)
            g = dd.terminal_growth
            if method == "perpetuity" and (w - g) <= 0.005:
                out[i, j] = np.nan
                continue
            out[i, j] = run_dcf(inputs, w, dd).per_share[method]
    return pd.DataFrame(out, index=row_vals, columns=col_vals)


def tornado(inputs: Inputs, base_wacc: float, d: Drivers) -> pd.DataFrame:
    """One-at-a-time swings on headline (blended) value/share."""
    base = run_dcf(inputs, base_wacc, d).headline
    tests = [
        ("WACC ±100bp", "wacc", base_wacc - 0.01, base_wacc + 0.01),
        ("Terminal growth ±100bp", "terminal_growth", d.terminal_growth - 0.01, d.terminal_growth + 0.01),
        ("Revenue growth ±200bp p.a.", "growth_shift", -0.02, 0.02),
        ("EBIT margin ±200bp", "margin_shift", -0.02, 0.02),
        ("Exit EV/EBITDA ±2.0x", "exit_multiple", d.exit_multiple - 2, d.exit_multiple + 2),
        ("NWC % revenue ±3pp", "nwc_pct", d.nwc_pct + 0.03, d.nwc_pct - 0.03),
        ("Tax rate ±300bp", "tax_rate", d.tax_rate + 0.03, d.tax_rate - 0.03),
    ]
    rows = []
    for label, p, lo, hi in tests:
        vals = []
        for v in (lo, hi):
            dd = Drivers(**d.__dict__)
            w = base_wacc
            if p == "wacc":
                w = v
            else:
                setattr(dd, p, v)
            vals.append(run_dcf(inputs, w, dd).headline)
        rows.append({"Driver": label, "Low case": min(vals), "High case": max(vals),
                     "Swing": abs(vals[1] - vals[0])})
    df = pd.DataFrame(rows).sort_values("Swing", ascending=True).reset_index(drop=True)
    df.attrs["base"] = base
    return df


# ---------------------------------------------------------------------------
# Monte Carlo
# ---------------------------------------------------------------------------
@dataclass
class MonteCarloResult:
    values: np.ndarray
    draws: pd.DataFrame
    stats: dict
    driver_correlations: pd.Series


def _nearest_psd(corr: np.ndarray) -> np.ndarray:
    vals, vecs = np.linalg.eigh(corr)
    vals = np.clip(vals, 1e-8, None)
    fixed = vecs @ np.diag(vals) @ vecs.T
    d = np.sqrt(np.diag(fixed))
    return fixed / np.outer(d, d)


def monte_carlo(inputs: Inputs, base_wacc: float, d: Drivers) -> MonteCarloResult:
    from scipy.stats import norm

    mc = inputs.config["monte_carlo"]
    n = int(mc["simulations"])
    rng = np.random.default_rng(mc["seed"])

    # Correlated standard normals via Cholesky (Gaussian copula).
    names = ["growth", "margin", "wacc", "tg", "exit"]
    c = np.eye(5)
    cc = mc["correlation"]
    c[0, 1] = c[1, 0] = cc["growth_margin"]
    c[2, 3] = c[3, 2] = cc["wacc_terminal_growth"]
    c[0, 4] = c[4, 0] = cc["growth_exit_multiple"]
    L = np.linalg.cholesky(_nearest_psd(c))
    z = rng.standard_normal((n, 5)) @ L.T

    growth = z[:, 0] * mc["growth_shift_sd"]
    margin = z[:, 1] * mc["margin_shift_sd"]
    wacc = base_wacc + z[:, 2] * mc["wacc_sd"]
    # Triangular terminal growth via inverse CDF of the correlated uniform.
    u = norm.cdf(z[:, 3])
    a, m, b = mc["terminal_growth"]["low"], mc["terminal_growth"]["mode"], mc["terminal_growth"]["high"]
    fc = (m - a) / (b - a)
    tg = np.where(u < fc, a + np.sqrt(u * (b - a) * (m - a)), b - np.sqrt((1 - u) * (b - a) * (b - m)))
    tg = np.minimum(tg, wacc - mc["min_spread_wacc_minus_g"])
    exit_m = np.clip(d.exit_multiple + z[:, 4] * mc["exit_multiple_sd"], 5.0, None)

    values = value_per_share_vectorized(inputs, d, growth, margin, wacc, tg, exit_m, method="blend")
    draws = pd.DataFrame({"Growth shift": growth, "Margin shift": margin, "WACC": wacc,
                          "Terminal growth": tg, "Exit multiple": exit_m, "Value/share": values})
    p = np.percentile(values, [5, 10, 25, 50, 75, 90, 95])
    stats = {
        "simulations": n, "mean": float(values.mean()), "std": float(values.std()),
        "p5": p[0], "p10": p[1], "p25": p[2], "p50": p[3], "p75": p[4], "p90": p[5], "p95": p[6],
        "prob_above_price": float((values > inputs.price).mean()),
        "prob_20pct_upside": float((values > inputs.price * 1.2).mean()),
        "prob_20pct_downside": float((values < inputs.price * 0.8).mean()),
        "price": inputs.price,
    }
    # Spearman rank correlation of each driver with value: which input drives the dispersion.
    ranks = draws.rank()
    corr = ranks.corr()["Value/share"].drop("Value/share").sort_values(key=np.abs, ascending=False)
    return MonteCarloResult(values=values, draws=draws, stats=stats, driver_correlations=corr)


# ---------------------------------------------------------------------------
# Trading comps
# ---------------------------------------------------------------------------
def comps(inputs: Inputs) -> dict:
    cfg = inputs.config["comps"]
    peers = pd.DataFrame(cfg["peers"]).set_index("name")
    snap = inputs.snapshot["ttm_inr_cr"]
    eps = snap["net_income"] / inputs.shares
    ebitda = snap["revenue"] * cfg["infosys_ttm_ebitda_margin"]
    net_claims = inputs.cash_and_investments - inputs.lease_liabilities - inputs.borrowings - inputs.minority_interest

    out = {"peers": peers, "ttm_eps": eps, "ttm_ebitda": ebitda,
           "infy_pe": inputs.price / eps,
           "infy_ev_ebitda": (inputs.market_cap - net_claims) / ebitda}
    for col, metric in (("pe_ttm", "P/E"), ("ev_ebitda_ttm", "EV/EBITDA")):
        s = peers[col].dropna().astype(float)
        q = {"low": s.quantile(0.25), "median": s.median(), "high": s.quantile(0.75), "min": s.min(), "max": s.max()}
        if metric == "P/E":
            implied = {k: v * eps for k, v in q.items()}
        else:
            implied = {k: (v * ebitda + net_claims) / inputs.shares for k, v in q.items()}
        out[metric] = {"multiples": q, "implied_price": implied, "n": int(len(s))}
    return out
