#!/usr/bin/env python3
"""Run the full Infosys valuation: DCF, scenarios, sensitivities, Monte Carlo, reverse DCF,
comps, charts and the formula-driven Excel model.

    python run_valuation.py                 # offline, from the bundled data snapshot
    python run_valuation.py --live          # refresh price, beta and peer multiples via yfinance
    python run_valuation.py --scenario bull # print a different scenario as the headline
    python run_valuation.py --sims 50000    # override Monte Carlo draws
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np

from dcf import charts
from dcf.analysis import comps, grid, monte_carlo, run_scenarios, tornado
from dcf.data import ROOT, load_inputs
from dcf.excel import build_workbook
from dcf.model import Drivers, reverse_dcf, run_dcf
from dcf.wacc import compute_wacc

log = logging.getLogger("dcf")


def rupee(x: float) -> str:
    return f"₹{x:,.0f}"


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "config" / "infosys.yaml"))
    ap.add_argument("--out", default=str(ROOT / "output"))
    ap.add_argument("--live", action="store_true", help="refresh market data via yfinance")
    ap.add_argument("--scenario", default="base", choices=["bear", "base", "bull"])
    ap.add_argument("--sims", type=int, default=None, help="Monte Carlo simulations")
    ap.add_argument("--no-excel", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # README charts live in docs/img; runs to any other output folder (e.g. tests) keep their own
    img = ROOT / "docs" / "img" if out.resolve() == (ROOT / "output").resolve() else out / "img"
    img.mkdir(parents=True, exist_ok=True)

    inputs = load_inputs(args.config)
    raw_beta, beta_src = None, "config assumption (Blume-adjusted)"
    if args.live:
        from dcf.live import refresh
        inputs = refresh(inputs)
        reg = inputs.live_overrides.get("beta_regression")
        if reg:
            raw_beta, beta_src = reg["beta"], "live 2y weekly regression vs NIFTY 50"
    if args.sims:
        inputs.config["monte_carlo"]["simulations"] = args.sims

    # ---- Cost of capital & core DCF ------------------------------------------------
    w = compute_wacc(inputs.config["wacc"], inputs.market_cap, inputs.lease_liabilities + inputs.borrowings,
                     raw_beta_override=raw_beta, beta_source=beta_src)
    d = Drivers.from_config(inputs.config, args.scenario)
    res = run_dcf(inputs, w.wacc, d)
    base_d = Drivers.from_config(inputs.config, "base")

    # ---- Analytics ------------------------------------------------------------------
    scen = run_scenarios(inputs, w.wacc)
    rev = reverse_dcf(inputs, w.wacc, base_d)
    mc = monte_carlo(inputs, w.wacc, base_d)
    torn = tornado(inputs, w.wacc, base_d)
    cp = comps(inputs)
    wacc_axis = np.round(w.wacc + np.arange(-0.015, 0.0151, 0.005), 4)
    g_axis = np.round(base_d.terminal_growth + np.arange(-0.01, 0.0101, 0.0025), 4)
    sens_g = grid(inputs, w.wacc, base_d, "wacc", wacc_axis, "terminal_growth", g_axis, "value_driver")
    mult_axis = base_d.exit_multiple + np.array([-3, -2, -1, 0, 1, 2, 3])
    sens_x = grid(inputs, w.wacc, base_d, "wacc", wacc_axis, "exit_multiple", mult_axis, "exit_multiple")
    shifts = [-0.02, -0.01, 0.0, 0.01, 0.02]
    mg = np.zeros((5, 5))
    for i, ms in enumerate(shifts):
        for j, gs in enumerate(shifts):
            dd = Drivers(**{**base_d.__dict__, "margin_shift": ms, "growth_shift": gs})
            mg[i, j] = run_dcf(inputs, w.wacc, dd).headline
    import pandas as pd
    mg = pd.DataFrame(mg, index=shifts, columns=shifts)

    # ---- Charts ---------------------------------------------------------------------
    base_res = run_dcf(inputs, w.wacc, base_d)
    ff_rows = [
        ("DCF: Value driver (WACC ±1%, g ±0.5%)", float(sens_g.iloc[1:6, 2:7].min().min()), float(sens_g.iloc[1:6, 2:7].max().max())),
        ("DCF: Exit multiple (WACC ±1%, ±1x)", float(sens_x.iloc[1:6, 2:5].min().min()), float(sens_x.iloc[1:6, 2:5].max().max())),
        ("DCF: Bear to Bull scenarios", float(scen.loc["bear", "Value / share (Blended)"]), float(scen.loc["bull", "Value / share (Blended)"])),
        ("Monte Carlo: P10 to P90", mc.stats["p10"], mc.stats["p90"]),
        ("Comps: P/E (IQR)", cp["P/E"]["implied_price"]["low"], cp["P/E"]["implied_price"]["high"]),
        ("Comps: EV/EBITDA (IQR)", cp["EV/EBITDA"]["implied_price"]["low"], cp["EV/EBITDA"]["implied_price"]["high"]),
        ("52-week trading range", inputs.snapshot["market"]["week52_low"], inputs.snapshot["market"]["week52_high"]),
    ]
    paths = {
        "football": img / "football_field.png", "mc": img / "monte_carlo.png", "tornado": img / "tornado.png",
        "heatmap": img / "sensitivity_wacc_g.png", "forecast": img / "forecast.png", "scenarios": img / "scenarios.png",
    }
    charts.football_field(ff_rows, inputs.price, paths["football"],
                          subtitle=f"Headline DCF {rupee(base_res.headline)} vs price {rupee(inputs.price)}")
    charts.mc_histogram(mc.values, mc.stats, paths["mc"])
    charts.tornado(torn, paths["tornado"])
    charts.sensitivity_heatmap(sens_g, inputs.price, paths["heatmap"], "WACC", "Terminal growth",
                               row_fmt="{:.2%}", col_fmt="{:.2%}",
                               title=f"Value-driver value per share (INR): blue above / red below price {rupee(inputs.price)}")
    charts.forecast_panels(inputs.historicals, base_res.forecast, paths["forecast"])
    charts.scenario_bars(scen, inputs.price, paths["scenarios"])

    # ---- Excel ----------------------------------------------------------------------
    xlsx = out / "Infosys_DCF_Model.xlsx"
    if not args.no_excel:
        build_workbook(xlsx, inputs, w, base_res, scen, mc, torn, cp, rev, mg, paths)

    # ---- Summary --------------------------------------------------------------------
    summary = {
        "valuation_date": str(inputs.valuation_date),
        "scenario": args.scenario,
        "price": inputs.price,
        "wacc": w.as_dict(),
        "per_share": {k: float(v) for k, v in res.per_share.items()},
        "headline_value": float(res.headline),
        "upside": float(res.upside),
        "checks": {k: (float(v) if not isinstance(v, dict) else {kk: float(vv) for kk, vv in v.items()})
                   for k, v in res.checks.items()},
        "reverse_dcf": {k: float(v) for k, v in rev.items()},
        "monte_carlo": {k: float(v) for k, v in mc.stats.items()},
        "scenarios": {k: float(scen.loc[k, "Value / share (Blended)"]) for k in scen.index},
        "comps": {m: {k: float(v) for k, v in cp[m]["implied_price"].items()} for m in ("P/E", "EV/EBITDA")},
        "live_overrides": inputs.live_overrides,
    }
    (out / "valuation_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    sens_g.to_csv(out / "sensitivity_wacc_vs_g.csv")
    sens_x.to_csv(out / "sensitivity_wacc_vs_exit.csv")
    base_res.forecast.to_csv(out / "forecast_base.csv")
    mc.draws.describe(percentiles=[.05, .1, .5, .9, .95]).to_csv(out / "monte_carlo_summary.csv")

    bar = "=" * 66
    print(f"\n{bar}\n  INFOSYS LTD (NSE: INFY) - DCF VALUATION  [{args.scenario.upper()}]\n{bar}")
    print(f"  Valuation date            {inputs.valuation_date}")
    print(f"  Market price              {rupee(inputs.price)}")
    print(f"  WACC / cost of equity     {res.wacc:.2%} / {w.cost_of_equity:.2%}  (beta {w.adjusted_beta:.2f}, {beta_src})")
    print(f"  Value/share - Gordon      {rupee(res.per_share['perpetuity'])}")
    print(f"  Value/share - Value driver {rupee(res.per_share['value_driver'])}")
    print(f"  Value/share - Exit {d.exit_multiple:.1f}x   {rupee(res.per_share['exit_multiple'])}")
    print(f"  HEADLINE (blend)          {rupee(res.headline)}  ({res.upside:+.1%})")
    print(f"  TV share of EV (Gordon)   {res.checks['tv_share_of_ev_perpetuity']:.0%};  Gordon implies "
          f"{res.checks['implied_exit_multiple_from_gordon']:.1f}x EBITDA")
    print(f"  Reverse DCF               market implies {rev['implied_revenue_cagr']:.1%} 10y revenue CAGR "
          f"(model: {rev['base_revenue_cagr']:.1%}) or a {rev['implied_wacc']:.2%} WACC")
    print(f"  Monte Carlo ({mc.stats['simulations']:,})       median {rupee(mc.stats['p50'])}, "
          f"P10-P90 {rupee(mc.stats['p10'])}-{rupee(mc.stats['p90'])}, P(> price) {mc.stats['prob_above_price']:.0%}")
    print(f"  Scenarios (blended)       " + " | ".join(f"{k} {rupee(scen.loc[k, 'Value / share (Blended)'])}" for k in scen.index))
    print(f"  Comps median              P/E {rupee(cp['P/E']['implied_price']['median'])}, "
          f"EV/EBITDA {rupee(cp['EV/EBITDA']['implied_price']['median'])}")
    print(bar)
    if not args.no_excel:
        print(f"  Excel model  -> {xlsx.relative_to(ROOT) if xlsx.is_relative_to(ROOT) else xlsx}")
    print(f"  Charts       -> docs/img/   Summary -> {out.name}/valuation_summary.json\n")
    return summary


if __name__ == "__main__":
    main()
