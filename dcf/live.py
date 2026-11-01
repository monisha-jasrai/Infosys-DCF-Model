"""Optional live refresh via yfinance (`python run_valuation.py --live`).

Refreshes: share price, share count, a 2-year weekly regression beta vs NIFTY 50, and
peer P/E and EV/EBITDA. Anything that fails falls back to the offline snapshot, and the
run log says which numbers are live and which are snapshot.
"""
from __future__ import annotations

import logging

from .data import Inputs
from .wacc import regression_beta

log = logging.getLogger(__name__)


def refresh(inputs: Inputs) -> Inputs:
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed; using snapshot data (pip install yfinance)")
        return inputs

    cfg = inputs.config
    out = inputs.copy()
    tkr = yf.Ticker(cfg["company"]["ticker"])

    # Price and shares
    try:
        info = tkr.fast_info
        price = float(info["last_price"])
        shares = float(info["shares"]) / 1e7  # to crore
        if price > 0 and shares > 0:
            out.price, out.shares = price, shares
            out.live_overrides.update({"price": price, "shares_cr": shares})
            log.info("live price %.2f, shares %.2f cr", price, shares)
    except Exception as e:  # noqa: BLE001
        log.warning("price refresh failed (%s); using snapshot", e)

    # Regression beta: 2y weekly returns vs NIFTY 50
    try:
        px = yf.download([cfg["company"]["ticker"], cfg["company"]["index_ticker"]],
                         period="2y", interval="1wk", auto_adjust=True, progress=False)["Close"]
        rets = px.pct_change().dropna()
        reg = regression_beta(rets[cfg["company"]["ticker"]], rets[cfg["company"]["index_ticker"]])
        out.live_overrides["beta_regression"] = reg
        log.info("regression beta %.2f (se %.2f, R2 %.2f, n=%d)", reg["beta"], reg["std_err"],
                 reg["r_squared"], reg["n"])
    except Exception as e:  # noqa: BLE001
        log.warning("beta regression failed (%s); using configured beta", e)

    # Peer multiples
    try:
        for peer in out.config["comps"]["peers"]:
            pinfo = yf.Ticker(peer["ticker"]).info
            if pinfo.get("trailingPE"):
                peer["pe_ttm"] = round(float(pinfo["trailingPE"]), 1)
            if pinfo.get("enterpriseToEbitda"):
                peer["ev_ebitda_ttm"] = round(float(pinfo["enterpriseToEbitda"]), 1)
        out.live_overrides["peers"] = "refreshed"
    except Exception as e:  # noqa: BLE001
        log.warning("peer refresh failed (%s); using snapshot multiples", e)
    return out
