"""Load assumptions and the point-in-time data snapshot, and build INR historicals.

Historical line items are reported in USD (IFRS, S&P-standardized). We translate each
fiscal year at the *implied average rate* (INR revenue / USD revenue), so every INR
line ties exactly to the company's reported INR revenue. Forecast drivers are ratios
(margin, % of revenue), which are currency-neutral.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
USD_MN_TO_INR_CR = 1 / 10.0  # (USD mn * INR/USD) = INR mn; / 10 = INR crore


@dataclass
class Inputs:
    """Everything the model needs, already in INR crore."""
    config: dict
    snapshot: dict
    historicals: pd.DataFrame          # rows = line items, cols = fiscal years
    base_revenue: float
    cash_and_investments: float
    lease_liabilities: float
    borrowings: float
    minority_interest: float
    price: float
    shares: float
    valuation_date: dt.date
    live_overrides: dict = field(default_factory=dict)

    @property
    def market_cap(self) -> float:
        return self.price * self.shares

    def copy(self) -> "Inputs":
        return copy.deepcopy(self)


def load_config(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_snapshot(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def operating_nwc_usd(bs: dict) -> list[float]:
    """Operating NWC = operating current assets - operating current liabilities (ex cash & debt)."""
    n = len(bs["receivables"])
    out = []
    for i in range(n):
        ca = bs["receivables"][i] + bs["inventory"][i] + bs["prepaid_expenses"][i] + bs["other_current_assets"][i]
        cl = (bs["accounts_payable"][i] + bs["accrued_expenses"][i] + bs["income_taxes_payable"][i]
              + bs["unearned_revenue"][i] + bs["other_current_liabilities"][i])
        out.append(ca - cl)
    return out


def build_historicals(snap: dict) -> pd.DataFrame:
    years = snap["fiscal_years"]
    inc, cf, bs = snap["income_statement_usd"], snap["cash_flow_usd"], snap["balance_sheet_usd"]
    rev_inr = snap["revenue_inr_cr"]
    fx = [rev_inr[i] / (inc["revenue"][i] * USD_MN_TO_INR_CR) for i in range(len(years))]

    def to_inr(series):
        return [series[i] * fx[i] * USD_MN_TO_INR_CR for i in range(len(years))]

    nwc_usd = operating_nwc_usd(bs)
    df = pd.DataFrame(
        {
            "Implied avg USD/INR": fx,
            "Revenue": rev_inr,
            "EBIT": to_inr(inc["ebit"]),
            "D&A": to_inr(cf["depreciation_amortization"]),
            "Capex": to_inr(cf["capex"]),
            "Lease payments": to_inr(cf["lease_payments"]),
            "Stock-based comp": to_inr(cf["stock_based_comp"]),
            "Operating cash flow": to_inr(cf["operating_cash_flow"]),
            "Net income": to_inr(inc["net_income"]),
            "Operating NWC": to_inr(nwc_usd),
        },
        index=years,
    ).T
    df.loc["Revenue growth"] = df.loc["Revenue"].pct_change()
    df.loc["EBIT margin"] = df.loc["EBIT"] / df.loc["Revenue"]
    df.loc["Effective tax rate"] = inc["effective_tax_rate"]
    df.loc["D&A % revenue"] = df.loc["D&A"] / df.loc["Revenue"]
    df.loc["Capex % revenue"] = df.loc["Capex"] / df.loc["Revenue"]
    df.loc["Lease payments % revenue"] = df.loc["Lease payments"] / df.loc["Revenue"]
    df.loc["NWC % revenue"] = df.loc["Operating NWC"] / df.loc["Revenue"]
    df.loc["SBC % revenue"] = df.loc["Stock-based comp"] / df.loc["Revenue"]
    return df


def load_inputs(config_path: str | Path = ROOT / "config" / "infosys.yaml") -> Inputs:
    cfg = load_config(config_path)
    snap = load_snapshot(ROOT / cfg["snapshot"])
    hist = build_historicals(snap)

    lb = snap["latest_balance_sheet"]
    conv = lb["fx_usdinr"] * USD_MN_TO_INR_CR
    usd = lb["usd_mn"]
    val_date = cfg["valuation_date"]
    if isinstance(val_date, str):
        val_date = dt.date.fromisoformat(val_date)

    return Inputs(
        config=cfg,
        snapshot=snap,
        historicals=hist,
        base_revenue=float(snap["revenue_inr_cr"][-1]),
        cash_and_investments=float(lb["cash_and_investments_inr_cr"]),
        lease_liabilities=usd["lease_liabilities"] * conv,
        borrowings=usd["borrowings"] * conv,
        minority_interest=usd["minority_interest"] * conv,
        price=float(snap["market"]["price"]),
        shares=float(snap["market"]["shares_diluted_cr"]),
        valuation_date=val_date,
    )
