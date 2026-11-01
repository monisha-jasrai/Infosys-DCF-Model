"""Cost of capital build-up.

Rf (INR)        = 10Y G-Sec - India sovereign default spread
Cost of equity  = Rf + beta_adj x Mature ERP + lambda x CRP
                  (Damodaran's lambda approach: country risk scaled by how exposed the
                   *business* is to India, rather than where it is listed)
Cost of debt    = pre-tax yield x (1 - t)   (Infosys has no borrowings; the "debt" is
                  IFRS 16 lease liabilities, priced off the 10Y AAA curve)
WACC            = E/V x Ke + D/V x Kd(1-t), market-value weights
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np


@dataclass
class WaccResult:
    gsec_10y: float
    default_spread: float
    risk_free: float
    raw_beta: float
    adjusted_beta: float
    mature_erp: float
    country_risk_premium: float
    lambda_: float
    cost_of_equity: float
    pre_tax_cost_of_debt: float
    after_tax_cost_of_debt: float
    equity_value: float
    debt_value: float
    weight_equity: float
    weight_debt: float
    wacc: float
    beta_source: str = "config"

    def as_dict(self) -> dict:
        return asdict(self)


def blume_adjust(raw_beta: float) -> float:
    """Blume (1971): betas mean-revert toward 1. Adjusted = 2/3 raw + 1/3."""
    return 0.67 * raw_beta + 0.33


def regression_beta(stock_returns, market_returns) -> dict:
    """OLS beta of stock on market returns, with standard error and R^2.

    Pure function so it can be unit-tested without network access; --live feeds it
    two years of weekly returns for INFY.NS vs ^NSEI.
    """
    y = np.asarray(stock_returns, dtype=float)
    x = np.asarray(market_returns, dtype=float)
    mask = ~(np.isnan(x) | np.isnan(y))
    x, y = x[mask], y[mask]
    if len(x) < 10:
        raise ValueError("need at least 10 paired observations")
    x_mean, y_mean = x.mean(), y.mean()
    sxx = ((x - x_mean) ** 2).sum()
    beta = ((x - x_mean) * (y - y_mean)).sum() / sxx
    alpha = y_mean - beta * x_mean
    resid = y - (alpha + beta * x)
    dof = len(x) - 2
    se = np.sqrt((resid ** 2).sum() / dof / sxx)
    r2 = 1 - (resid ** 2).sum() / ((y - y_mean) ** 2).sum()
    return {"beta": float(beta), "alpha": float(alpha), "std_err": float(se), "r_squared": float(r2), "n": int(len(x))}


def compute_wacc(cfg_wacc: dict, equity_value: float, debt_value: float,
                 raw_beta_override: float | None = None, beta_source: str = "config") -> WaccResult:
    raw = raw_beta_override if raw_beta_override is not None else cfg_wacc["beta"]["raw"]
    adj = blume_adjust(raw) if cfg_wacc["beta"].get("blume_adjust", True) else raw

    rf = cfg_wacc["gsec_10y"] - cfg_wacc.get("india_default_spread", 0.0)
    ke = (rf
          + adj * cfg_wacc["mature_market_erp"]
          + cfg_wacc["lambda"] * cfg_wacc["country_risk_premium"])
    kd_pre = cfg_wacc["pre_tax_cost_of_debt"]
    kd = kd_pre * (1 - cfg_wacc["marginal_tax_rate"])

    v = equity_value + debt_value
    we, wd = equity_value / v, debt_value / v
    return WaccResult(
        gsec_10y=cfg_wacc["gsec_10y"], default_spread=cfg_wacc.get("india_default_spread", 0.0),
        risk_free=rf, raw_beta=raw, adjusted_beta=adj,
        mature_erp=cfg_wacc["mature_market_erp"], country_risk_premium=cfg_wacc["country_risk_premium"],
        lambda_=cfg_wacc["lambda"], cost_of_equity=ke, pre_tax_cost_of_debt=kd_pre,
        after_tax_cost_of_debt=kd, equity_value=equity_value, debt_value=debt_value,
        weight_equity=we, weight_debt=wd, wacc=we * ke + wd * kd, beta_source=beta_source,
    )
