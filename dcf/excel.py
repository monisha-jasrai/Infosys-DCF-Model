"""Export a banker-style, formula-driven Excel model.

Conventions: blue = hard-coded input, black = formula, green = link to another sheet,
yellow fill = key lever. Every DCF, WACC, sensitivity and comps number is a live formula,
so the workbook re-prices when an input changes. Tabs tagged [Python] hold outputs of the
Python engine (Monte Carlo, scenario table, reverse DCF) that Excel cannot natively compute;
re-run `python run_valuation.py` to refresh them.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.drawing.image import Image as XLImage
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as col

from .data import operating_nwc_usd

FONT = "Arial"
NAVY = "1F3864"
F_TITLE = Font(name=FONT, size=16, bold=True, color=NAVY)
F_SUB = Font(name=FONT, size=10, italic=True, color="595959")
F_HDR = Font(name=FONT, size=10, bold=True, color="FFFFFF")
F_SEC = Font(name=FONT, size=10, bold=True, color=NAVY)
F_LBL = Font(name=FONT, size=10)
F_BOLD = Font(name=FONT, size=10, bold=True)
F_IN = Font(name=FONT, size=10, color="0000FF")
F_LINK = Font(name=FONT, size=10, color="008000")
F_CALC = Font(name=FONT, size=10, color="000000")
F_CALC_B = Font(name=FONT, size=10, color="000000", bold=True)
F_NOTE = Font(name=FONT, size=9, italic=True, color="595959")
FILL_HDR = PatternFill("solid", fgColor=NAVY)
FILL_SEC = PatternFill("solid", fgColor="D9E1F2")
FILL_KEY = PatternFill("solid", fgColor="FFFF00")
FILL_OUT = PatternFill("solid", fgColor="E2EFDA")
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(top=THIN, bottom=THIN, left=THIN, right=THIN)
TOPLINE = Border(top=Side(style="thin", color="000000"))

NUM = '#,##0;(#,##0);"-"'
NUM1 = '#,##0.0;(#,##0.0);"-"'
RS = '"₹"#,##0;("₹"#,##0);"-"'
RS2 = '"₹"#,##0.00'
PCT = '0.0%;(0.0%);"-"'
PCT2 = '0.00%;(0.00%);"-"'
MULT = '0.0"x"'
DEC2 = '0.00'
DEC3 = '0.000'


def _w(ws, ref, value, font=F_CALC, fmt=None, fill=None, bold=False, align=None, border=None):
    c = ws[ref]
    c.value = value
    c.font = Font(name=font.name, size=font.size, bold=bold or font.bold, italic=font.italic, color=font.color)
    if fmt:
        c.number_format = fmt
    if fill:
        c.fill = fill
    if align:
        c.alignment = Alignment(horizontal=align, vertical="center")
    if border:
        c.border = border
    return c


def _title(ws, title, subtitle):
    _w(ws, "A1", title, F_TITLE)
    _w(ws, "A2", subtitle, F_SUB)
    ws.sheet_view.showGridLines = False


def _hdr(ws, row, labels, start_col=1):
    for i, lab in enumerate(labels):
        c = ws.cell(row=row, column=start_col + i, value=lab)
        c.font, c.fill = F_HDR, FILL_HDR
        c.alignment = Alignment(horizontal="center" if i else "left", vertical="center")


def _sec(ws, row, text, width=12):
    for i in range(1, width + 1):
        ws.cell(row=row, column=i).fill = FILL_SEC
    c = ws.cell(row=row, column=1, value=text)
    c.font = F_SEC


def _note(ws, ref, text):
    c = ws[ref]
    c.comment = Comment(text, "DCF model")


# ---------------------------------------------------------------------------
def build_workbook(path: Path, inputs, wacc_res, base_result, scen_df, mc, tornado_df, comps_res,
                   reverse, margin_growth_grid, charts: dict):
    cfg = inputs.config
    snap = inputs.snapshot
    wb = Workbook()
    ws_cover = wb.active
    ws_cover.title = "Cover"
    ws_in = wb.create_sheet("Inputs")
    ws_h = wb.create_sheet("Historicals")
    ws_w = wb.create_sheet("WACC")
    ws_d = wb.create_sheet("DCF")
    ws_s = wb.create_sheet("Sensitivity")
    ws_sc = wb.create_sheet("Scenarios")
    ws_mc = wb.create_sheet("Monte Carlo")
    ws_c = wb.create_sheet("Comps")
    ws_ff = wb.create_sheet("Football Field")
    n = cfg["forecast"]["years"]

    # ======================= INPUTS =======================
    ws = ws_in
    _title(ws, "Inputs & assumptions", "Blue = hard-coded input  |  Green = link  |  Yellow = key lever. "
           "All money in INR crore unless stated.")
    ws.column_dimensions["A"].width = 38
    for c_ in "BCDEFGHIJK":
        ws.column_dimensions[c_].width = 12
    ws.column_dimensions["L"].width = 60

    _sec(ws, 4, "Market & balance sheet")
    lb = snap["latest_balance_sheet"]
    rows = [
        (5, "Valuation date", inputs.valuation_date, "yyyy-mm-dd", F_IN, "Model valuation date"),
        (6, "Fiscal year-end (FY27)", dt.date(inputs.valuation_date.year + (1 if inputs.valuation_date.month > 3 else 0), 3, 31),
         "yyyy-mm-dd", F_IN, "Infosys fiscal year ends 31 March"),
        (7, "Share price (₹)", inputs.price, RS2, F_IN, f"NSE close {snap['market']['price_date']}"),
        (8, "Diluted shares (crore)", inputs.shares, DEC2, F_IN, snap["market"]["shares_note"]),
        (9, "Cash & investments", inputs.cash_and_investments, NUM, F_IN,
         lb["cash_note"]),
        (10, "Lease liabilities (IFRS 16)", inputs.lease_liabilities, NUM, F_IN, f"At {lb['usd_items_as_of']}, USD {lb['usd_mn']['lease_liabilities']} mn x {lb['fx_usdinr']}"),
        (11, "Borrowings", inputs.borrowings, NUM, F_IN, "Infosys has no financial debt"),
        (12, "Minority interest", inputs.minority_interest, NUM, F_IN, f"USD {lb['usd_mn']['minority_interest']} mn x {lb['fx_usdinr']}"),
        (13, "Base-year revenue FY26A", "=Historicals!G16", NUM, F_LINK, "Reported INR revenue FY26"),
    ]
    for r, lab, val, fmt, font, note in rows:
        _w(ws, f"A{r}", lab, F_LBL)
        _w(ws, f"B{r}", val, font, fmt)
        _w(ws, f"L{r}", note, F_NOTE)

    _sec(ws, 15, "Cost of capital")
    wc = cfg["wacc"]
    rows = [
        (16, "10Y G-Sec yield", wc["gsec_10y"], PCT2, "10Y G-Sec benchmark 7.40% on 14-Oct-2026 (ICRA via ABSL tracker)"),
        (17, "Mature-market ERP", wc["mature_market_erp"], PCT2, "Damodaran, Jan 2026"),
        (18, "India country risk premium", wc["country_risk_premium"], PCT2, "Damodaran, Jan 2026 (Baa3)"),
        (19, "Lambda (India exposure)", wc["lambda"], DEC2, "~97% of revenue exported; cost base in India -> partial exposure"),
        (20, "Raw beta", wacc_res.raw_beta, DEC2, f"Source: {wacc_res.beta_source}. --live runs 2y weekly regression vs NIFTY 50"),
        (21, "Blume adjustment (1 = on)", 1 if wc["beta"].get("blume_adjust", True) else 0, "0", "Adjusted = 0.67 x raw + 0.33"),
        (22, "Pre-tax cost of debt (leases)", wc["pre_tax_cost_of_debt"], PCT2, "10Y AAA corporate yield 8.04%, 14-Oct-2026"),
        (23, "Marginal tax rate", wc["marginal_tax_rate"], PCT, "India corporate tax incl. surcharge ~25-26%; FY26 ETR 26.4%"),
        (24, "India default spread (deducted from G-Sec)", wc.get("india_default_spread", 0.0), PCT2,
         "Damodaran, Jan 2026: 1.87%. G-Sec already prices default risk; set to 0 to use the raw G-Sec yield"),
    ]
    for r, lab, val, fmt, note in rows:
        _w(ws, f"A{r}", lab, F_LBL)
        _w(ws, f"B{r}", val, F_IN, fmt)
        _w(ws, f"L{r}", note, F_NOTE)

    _sec(ws, 25, "Operating drivers (FY27E-FY36E)")
    f = cfg["forecast"]
    t = cfg["terminal"]
    rows = [
        (26, "EBIT margin - FY27E", f["ebit_margin_start"], PCT, "FY27 guidance 20-22%; H1 FY27 actual 21.0%", True),
        (27, "EBIT margin - FY36E", f["ebit_margin_end"], PCT, "Linear fade between start and end", True),
        (28, "Cash tax rate on EBIT", f["tax_rate"], PCT, "FY26 ETR 26.4%; 5y average 27.4%", False),
        (29, "D&A % revenue (incl. ROU)", f["da_pct_revenue"], PCT, "FY26 2.7%", False),
        (30, "Capex % revenue", f["capex_pct_revenue"], PCT, "FY22-26 average 1.6%", False),
        (31, "ROU (lease) additions % revenue", f["rou_additions_pct_revenue"], PCT, "~ lease payments, FY26 1.6% (IFRS 16 consistency)", False),
        (32, "Operating NWC % revenue", f["nwc_pct_revenue"], PCT, "FY22-26 average 11.2%; FY26 10.0%", False),
        (33, "RONIC (value-driver TV)", t["ronic"], PCT, "Return on new invested capital in steady state", False),
        (34, "Headline weight: Gordon", cfg["headline_weights"].get("perpetuity", 0), PCT, "Gordon on FCFF implies ~80% return on new capital, so shown as a check only", False),
        (35, "Headline weight: Exit multiple", cfg["headline_weights"].get("exit_multiple", 0), PCT, "", False),
        (36, "Headline weight: Value driver", cfg["headline_weights"].get("value_driver", 0), PCT, "Reinvestment tied to growth via RONIC", False),
    ]
    for r, lab, val, fmt, note, key in rows:
        _w(ws, f"A{r}", lab, F_LBL)
        _w(ws, f"B{r}", val, F_IN, fmt, fill=FILL_KEY if key else None)
        _w(ws, f"L{r}", note, F_NOTE)

    _sec(ws, 37, "Revenue growth path - base case (INR, nominal)")
    years = [f"FY{int(inputs.historicals.columns[-1][2:]) + i}E" for i in range(1, n + 1)]
    for i, (y, g) in enumerate(zip(years, f["revenue_growth"])):
        _w(ws, f"{col(2 + i)}38", y, F_BOLD, align="center")
        _w(ws, f"{col(2 + i)}39", g, F_IN, PCT, fill=FILL_KEY)
    _w(ws, "A39", "Revenue growth", F_LBL)
    _w(ws, "L39", "FY27E: H1 +12.3% INR YoY; CC guidance 1-2%. Long run = CC growth + INR depreciation", F_NOTE)

    _sec(ws, 41, "Scenario selector")
    _w(ws, "A42", "Active scenario (1 = Bear, 2 = Base, 3 = Bull)", F_BOLD)
    _w(ws, "B42", 2, F_IN, "0", fill=FILL_KEY, align="center")
    _w(ws, "C42", '=CHOOSE(B42,"Bear","Base","Bull")', F_CALC_B)
    _note(ws, "B42", "Change to 1 or 3: every live sheet (DCF, WACC, Sensitivity, Cover) re-prices.")
    _hdr(ws, 44, ["Lever", "Bear", "Base", "Bull", "Active"])
    levers = [("Growth shift (added to every year)", "growth_shift", PCT),
              ("EBIT margin shift", "margin_shift", PCT),
              ("WACC shift", "wacc_shift", PCT2),
              ("Terminal growth", "terminal_growth", PCT),
              ("Exit EV/EBITDA", "exit_ev_ebitda", MULT)]
    for k, (lab, key, fmt) in enumerate(levers):
        r = 45 + k
        _w(ws, f"A{r}", lab, F_LBL)
        for j, sc in enumerate(("bear", "base", "bull")):
            _w(ws, f"{col(2 + j)}{r}", cfg["scenarios"][sc][key], F_IN, fmt)
        _w(ws, f"E{r}", f"=INDEX(B{r}:D{r},1,$B$42)", F_CALC_B, fmt, fill=FILL_OUT)
    # Active-lever cells used downstream
    A_GS, A_MS, A_WS, A_TG, A_EX = "Inputs!$E$45", "Inputs!$E$46", "Inputs!$E$47", "Inputs!$E$48", "Inputs!$E$49"

    # ======================= HISTORICALS =======================
    ws = ws_h
    _title(ws, "Historical financials FY22-FY26", "USD lines as reported (S&P-standardized IFRS); translated to INR "
           "at each year's implied average rate so revenue ties to reported INR revenue.")
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 14
    for c_ in "CDEFG":
        ws.column_dimensions[c_].width = 13
    hy = snap["fiscal_years"]
    _hdr(ws, 3, ["Line item", "Units"] + hy)
    inc, cf = snap["income_statement_usd"], snap["cash_flow_usd"]
    nwc_usd = [round(x) for x in operating_nwc_usd(snap["balance_sheet_usd"])]
    raw = [
        (4, "Revenue", "USD mn", inc["revenue"], NUM),
        (5, "Revenue (reported INR)", "₹ crore", snap["revenue_inr_cr"], NUM),
        (7, "EBIT", "USD mn", inc["ebit"], NUM),
        (8, "D&A (incl. ROU)", "USD mn", cf["depreciation_amortization"], NUM),
        (9, "Capex", "USD mn", cf["capex"], NUM),
        (10, "Lease payments", "USD mn", cf["lease_payments"], NUM),
        (11, "Stock-based comp", "USD mn", cf["stock_based_comp"], NUM),
        (12, "Operating NWC", "USD mn", nwc_usd, NUM),
        (13, "Effective tax rate", "%", inc["effective_tax_rate"], PCT),
        (14, "Operating cash flow", "USD mn", cf["operating_cash_flow"], NUM),
    ]
    for r, lab, unit, vals, fmt in raw:
        _w(ws, f"A{r}", lab, F_LBL)
        _w(ws, f"B{r}", unit, F_NOTE)
        for i, v in enumerate(vals):
            _w(ws, f"{col(3 + i)}{r}", v, F_IN, fmt)
    _note(ws, "A12", "Receivables + inventory + prepaid + other current assets - payables - accrued - "
                     "income tax payable - unearned revenue - other current liabilities")
    _w(ws, "A6", "Implied avg USD/INR", F_LBL)
    _w(ws, "B6", "INR per USD", F_NOTE)
    for i in range(5):
        c_ = col(3 + i)
        _w(ws, f"{c_}6", f"={c_}5/({c_}4/10)", F_CALC, DEC2)
    _sec(ws, 15, "Translated to INR crore", 7)
    conv = [(16, "Revenue", 5, False), (17, "EBIT", 7, True), (18, "D&A", 8, True), (19, "Capex", 9, True),
            (20, "Lease payments", 10, True), (21, "Stock-based comp", 11, True), (22, "Operating NWC", 12, True),
            (23, "Operating cash flow", 14, True)]
    for r, lab, src, fx in conv:
        _w(ws, f"A{r}", lab, F_LBL)
        _w(ws, f"B{r}", "₹ crore", F_NOTE)
        for i in range(5):
            c_ = col(3 + i)
            _w(ws, f"{c_}{r}", f"={c_}{src}*{c_}$6/10" if fx else f"={c_}{src}", F_CALC, NUM)
    _sec(ws, 25, "Ratios (forecast anchors)", 7)
    ratios = [(26, "Revenue growth (INR)", None), (27, "EBIT margin", 17), (28, "D&A % revenue", 18),
              (29, "Capex % revenue", 19), (30, "Lease payments % revenue", 20), (31, "NWC % revenue", 22),
              (32, "SBC % revenue", 21), (33, "FCF (CFO - capex - leases) % revenue", None)]
    for r, lab, src in ratios:
        _w(ws, f"A{r}", lab, F_LBL)
        for i in range(5):
            c_ = col(3 + i)
            if r == 26:
                if i:
                    _w(ws, f"{c_}{r}", f"={c_}16/{col(2 + i)}16-1", F_CALC, PCT)
            elif r == 33:
                _w(ws, f"{c_}{r}", f"=({c_}23-{c_}19-{c_}20)/{c_}16", F_CALC, PCT)
            else:
                _w(ws, f"{c_}{r}", f"={c_}{src}/{c_}16", F_CALC, PCT)
        _w(ws, f"H{r}", f"=AVERAGE({'D' if r == 26 else 'C'}{r}:G{r})", F_CALC_B, PCT)
    _w(ws, "H25", "Average", F_SEC)
    _w(ws, "A35", "Source: stockanalysis.com (S&P Global Market Intelligence), updated 23-Jul-2026; "
       "INR revenue from Infosys IFRS INR press releases (Form 6-K).", F_NOTE)

    # ======================= WACC =======================
    ws = ws_w
    _title(ws, "Cost of capital (WACC)", "Rf = G-Sec - default spread   |   Ke = Rf + beta_adj x Mature ERP + lambda x CRP   |   market-value weights")
    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 60
    rows = [
        (3, "10Y G-Sec yield", "=Inputs!B16", PCT2, F_LINK, ""),
        (4, "Risk-free rate (G-Sec less default spread)", "=Inputs!B16-Inputs!B24", PCT2, F_CALC, "Damodaran local-currency approach"),
        (5, "Raw beta", "=Inputs!B20", DEC2, F_LINK, ""),
        (6, "Adjusted beta", "=IF(Inputs!B21=1,0.67*B5+0.33,B5)", DEC3, F_CALC, "Blume: mean reversion toward 1.0"),
        (7, "Mature-market ERP", "=Inputs!B17", PCT2, F_LINK, ""),
        (8, "Lambda", "=Inputs!B19", DEC2, F_LINK, ""),
        (9, "Country risk premium", "=Inputs!B18", PCT2, F_LINK, ""),
        (10, "Cost of equity", "=B4+B6*B7+B8*B9", PCT2, F_CALC_B, "CAPM + lambda-weighted CRP"),
        (12, "Pre-tax cost of debt", "=Inputs!B22", PCT2, F_LINK, ""),
        (13, "Tax rate", "=Inputs!B23", PCT, F_LINK, ""),
        (14, "After-tax cost of debt", "=B12*(1-B13)", PCT2, F_CALC_B, ""),
        (16, "Market value of equity", "=Inputs!B7*Inputs!B8", NUM, F_CALC, "Price x diluted shares"),
        (17, "Debt (lease liabilities + borrowings)", "=Inputs!B10+Inputs!B11", NUM, F_CALC, ""),
        (18, "Weight of equity", "=B16/(B16+B17)", PCT, F_CALC, ""),
        (19, "Weight of debt", "=B17/(B16+B17)", PCT, F_CALC, ""),
        (21, "WACC - base", "=B18*B10+B19*B14", PCT2, F_CALC_B, ""),
        (22, "Scenario WACC shift", f"={A_WS}", PCT2, F_LINK, "From Inputs scenario selector"),
        (23, "WACC used in DCF", "=B21+B22", PCT2, F_CALC_B, ""),
    ]
    for r, lab, val, fmt, font, note in rows:
        _w(ws, f"A{r}", lab, F_BOLD if font.bold else F_LBL)
        _w(ws, f"B{r}", val, font, fmt, fill=FILL_OUT if r in (10, 21, 23) else None)
        _w(ws, f"C{r}", note, F_NOTE)
    if inputs.live_overrides.get("beta_regression"):
        reg = inputs.live_overrides["beta_regression"]
        _w(ws, "A25", f"Live regression: beta {reg['beta']:.2f}, s.e. {reg['std_err']:.2f}, R² {reg['r_squared']:.2f}, n={reg['n']} weekly obs vs NIFTY 50", F_NOTE)

    # ======================= DCF =======================
    ws = ws_d
    _title(ws, "Discounted cash flow - FCFF", "Mid-year convention with a stub period from the valuation date to "
           "31-Mar-2027. INR crore.")
    ws.column_dimensions["A"].width = 48
    for i in range(2, 2 + n + 1):
        ws.column_dimensions[col(i)].width = 12
    ws.freeze_panes = "B4"
    _hdr(ws, 3, ["INR crore", inputs.historicals.columns[-1] + "A"] + years)
    L = col(2 + n)        # last forecast column
    C1 = "C"
    _w(ws, "A4", "Forecast year #", F_NOTE)
    for i in range(n):
        _w(ws, f"{col(3 + i)}4", i + 1, F_NOTE, "0", align="center")
    lines = ["Revenue growth", "Revenue", "EBIT margin", "EBIT", "(-) Taxes on EBIT", "NOPAT", "(+) D&A",
             "EBITDA", "(-) Capex", "(-) ROU (lease) additions", "Operating NWC", "(-) Increase in NWC",
             "Unlevered free cash flow (FCFF)"]
    for k, lab in enumerate(lines):
        _w(ws, f"A{5 + k}", lab, F_BOLD if k in (1, 12) else F_LBL)
    _w(ws, "B6", "=Inputs!B13", F_LINK, NUM)
    _w(ws, "B15", "=B6*Inputs!$B$32", F_CALC, NUM)
    for i in range(n):
        c_, p_ = col(3 + i), col(2 + i)
        gref = f"Inputs!{col(2 + i)}$39"
        _w(ws, f"{c_}5", f"={gref}+{A_GS}", F_CALC, PCT)
        _w(ws, f"{c_}6", f"={p_}6*(1+{c_}5)", F_CALC_B, NUM)
        _w(ws, f"{c_}7", f"=Inputs!$B$26+(Inputs!$B$27-Inputs!$B$26)*({c_}4-1)/(COUNT($C$4:${L}$4)-1)+{A_MS}", F_CALC, PCT)
        _w(ws, f"{c_}8", f"={c_}6*{c_}7", F_CALC, NUM)
        _w(ws, f"{c_}9", f"=-{c_}8*Inputs!$B$28", F_CALC, NUM)
        _w(ws, f"{c_}10", f"={c_}8+{c_}9", F_CALC, NUM)
        _w(ws, f"{c_}11", f"={c_}6*Inputs!$B$29", F_CALC, NUM)
        _w(ws, f"{c_}12", f"={c_}8+{c_}11", F_CALC, NUM)
        _w(ws, f"{c_}13", f"=-{c_}6*Inputs!$B$30", F_CALC, NUM)
        _w(ws, f"{c_}14", f"=-{c_}6*Inputs!$B$31", F_CALC, NUM)
        _w(ws, f"{c_}15", f"={c_}6*Inputs!$B$32", F_CALC, NUM)
        _w(ws, f"{c_}16", f"=-({c_}15-{p_}15)", F_CALC, NUM)
        _w(ws, f"{c_}17", f"={c_}10+{c_}11+{c_}13+{c_}14+{c_}16", F_CALC_B, NUM, border=TOPLINE)
    _w(ws, "A18", "FCFF margin", F_NOTE)
    for i in range(n):
        c_ = col(3 + i)
        _w(ws, f"{c_}18", f"={c_}17/{c_}6", F_NOTE, PCT)

    _sec(ws, 20, "Discounting", 2 + n)
    _w(ws, "A21", "Stub fraction of FY27 remaining", F_LBL)
    _w(ws, "B21", "=(Inputs!B6-Inputs!B5)/365", F_CALC, DEC3)
    _w(ws, "A22", "Cash-flow weight", F_LBL)
    _w(ws, "A23", "Discount period (mid-year)", F_LBL)
    _w(ws, "A24", "WACC", F_LBL)
    _w(ws, "B24", "=WACC!B23", F_LINK, PCT2)
    _w(ws, "A25", "Discount factor", F_LBL)
    _w(ws, "A26", "PV of FCFF", F_BOLD)
    for i in range(n):
        c_ = col(3 + i)
        _w(ws, f"{c_}22", "=$B$21" if i == 0 else 1, F_CALC if i == 0 else F_IN, DEC3)
        _w(ws, f"{c_}23", "=$B$21/2" if i == 0 else f"=$B$21+({c_}4-2)+0.5", F_CALC, DEC3)
        _w(ws, f"{c_}25", f"=1/(1+$B$24)^{c_}23", F_CALC, DEC3)
        _w(ws, f"{c_}26", f"={c_}17*{c_}22*{c_}25", F_CALC_B, NUM)

    _sec(ws, 28, "Terminal value", 2 + n)
    tv_rows = [
        (29, "Terminal growth (g)", f"={A_TG}", PCT, F_LINK),
        (30, "Exit EV/EBITDA", f"={A_EX}", MULT, F_LINK),
        (31, "RONIC (value-driver)", "=Inputs!B33", PCT, F_LINK),
        (32, "Years to end of final forecast year", f"=$B$21+COUNT($C$4:${L}$4)-1", DEC3, F_CALC),
        (33, "TV - Gordon growth: FCFF(N)x(1+g)/(WACC-g)", f"={L}17*(1+B29)/(B24-B29)", NUM, F_CALC),
        (34, "PV of TV - Gordon (mid-year factor)", f"=B33*{L}25", NUM, F_CALC_B),
        (35, "TV - Value driver: NOPAT(1+g)(1-g/RONIC)/(WACC-g)", f"={L}10*(1+B29)*(1-B29/B31)/(B24-B29)", NUM, F_CALC),
        (36, "PV of TV - Value driver", f"=B35*{L}25", NUM, F_CALC_B),
        (37, "TV - Exit multiple: EBITDA(N) x multiple", f"={L}12*B30", NUM, F_CALC),
        (38, "PV of TV - Exit (end of year N)", "=B37/(1+B24)^B32", NUM, F_CALC_B),
    ]
    for r, lab, val, fmt, font in tv_rows:
        _w(ws, f"A{r}", lab, F_LBL)
        _w(ws, f"B{r}", val, font, fmt)

    _sec(ws, 40, "Enterprise value to equity value per share", 2 + n)
    _hdr(ws, 41, ["INR crore", "Gordon", "Value driver", "Exit multiple"])
    bridge = [
        (42, "PV of explicit FCFF", [f"=SUM($C$26:${L}$26)"] * 3),
        (43, "PV of terminal value", ["=B34", "=B36", "=B38"]),
        (44, "Enterprise value", ["=B42+B43", "=C42+C43", "=D42+D43"]),
        (45, "(+) Cash & investments", ["=Inputs!$B$9"] * 3),
        (46, "(-) Lease liabilities", ["=-Inputs!$B$10"] * 3),
        (47, "(-) Borrowings", ["=-Inputs!$B$11"] * 3),
        (48, "(-) Minority interest", ["=-Inputs!$B$12"] * 3),
        (49, "Equity value", ["=SUM(B44:B48)", "=SUM(C44:C48)", "=SUM(D44:D48)"]),
        (50, "Diluted shares (crore)", ["=Inputs!$B$8"] * 3),
        (51, "Intrinsic value per share (₹)", ["=B49/B50", "=C49/C50", "=D49/D50"]),
        (52, "Upside / (downside) vs price", ["=B51/Inputs!$B$7-1", "=C51/Inputs!$B$7-1", "=D51/Inputs!$B$7-1"]),
        (53, "Terminal value % of EV", ["=B43/B44", "=C43/C44", "=D43/D44"]),
    ]
    for r, lab, vals in bridge:
        _w(ws, f"A{r}", lab, F_BOLD if r in (44, 49, 51) else F_LBL)
        for j, v in enumerate(vals):
            fmt = RS if r == 51 else (PCT if r in (52, 53) else (DEC2 if r == 50 else NUM))
            font = F_LINK if v.startswith("=Inputs") or v.startswith("=-Inputs") else (F_CALC_B if r in (44, 49, 51) else F_CALC)
            _w(ws, f"{col(2 + j)}{r}", v, font, fmt, fill=FILL_OUT if r == 51 else None,
               border=TOPLINE if r in (44, 49) else None)

    _sec(ws, 55, "Headline & cross-checks", 2 + n)
    heads = [
        (56, "Headline intrinsic value (weighted blend)", "=(B51*Inputs!B34+D51*Inputs!B35+C51*Inputs!B36)/(Inputs!B34+Inputs!B35+Inputs!B36)", RS),
        (57, "Upside / (downside) vs market price", "=B56/Inputs!B7-1", PCT),
        (58, "Implied exit multiple from Gordon TV", f"=B33*(1+B24)^(B32-{L}23)/{L}12", MULT),
        (59, "Implied perpetual growth from exit multiple", f"=(B37/(1+B24)^(B32-{L}23)*B24-{L}17)/(B37/(1+B24)^(B32-{L}23)+{L}17)", PCT2),
        (60, "Terminal-year reinvestment rate (1 - FCFF/NOPAT)", f"=1-{L}17/{L}10", PCT),
        (61, "Implied RONIC (g / reinvestment rate)", "=B29/B60", PCT),
    ]
    for r, lab, val, fmt in heads:
        _w(ws, f"A{r}", lab, F_BOLD if r in (56, 57) else F_LBL)
        _w(ws, f"B{r}", val, F_CALC_B, fmt, fill=FILL_KEY if r == 56 else None)
    _note(ws, "B58", "If this is far below the exit multiple you chose, the exit multiple is doing heavy lifting "
                     "(it implies higher growth than the Gordon g). See B59.")

    # ======================= SENSITIVITY =======================
    ws = ws_s
    _title(ws, "Sensitivity analysis (live formulas)", "Each cell re-prices the whole DCF: "
           "SUMPRODUCT of FCFF x weights x discount factors + PV of TV + net cash, / shares.")
    ws.column_dimensions["A"].width = 22
    for i in range(2, 12):
        ws.column_dimensions[col(i)].width = 11
    claims = "(Inputs!$B$9-Inputs!$B$10-Inputs!$B$11-Inputs!$B$12)"
    pv_exp = lambda w: f"SUMPRODUCT(DCF!$C$17:${L}$17,DCF!$C$22:${L}$22,1/(1+{w})^DCF!$C$23:${L}$23)"
    price = inputs.price

    def grid_block(top, title, col_label, col_offsets, col_base, col_fmt, tv_expr):
        _sec(ws, top, title, 9)
        _w(ws, f"A{top + 1}", f"WACC ↓  /  {col_label} →", F_NOTE)
        for j, off in enumerate(col_offsets):
            _w(ws, f"{col(2 + j)}{top + 1}", f"={col_base}+({off})", F_CALC_B, col_fmt, align="center", fill=FILL_SEC)
        w_offs = [-0.015, -0.01, -0.005, 0, 0.005, 0.01, 0.015]
        for i, off in enumerate(w_offs):
            r = top + 2 + i
            _w(ws, f"A{r}", f"=DCF!$B$24+({off})", F_CALC_B, PCT2, fill=FILL_SEC, align="center")
            for j in range(len(col_offsets)):
                cc = f"{col(2 + j)}${top + 1}"
                w = f"$A{r}"
                _w(ws, f"{col(2 + j)}{r}", f"=({pv_exp(w)}+{tv_expr(w, cc)}+{claims})/Inputs!$B$8",
                   F_CALC, RS, border=BOX, align="center")
        rng = f"B{top + 2}:{col(1 + len(col_offsets))}{top + 8}"
        ws.conditional_formatting.add(rng, ColorScaleRule(start_type="min", start_color="F8A5A4",
                                                          mid_type="num", mid_value=price, mid_color="F0EFEC",
                                                          end_type="max", end_color="86B6EF"))

    grid_block(4, "Value driver: value per share (₹) - WACC vs terminal growth", "Terminal growth",
               [-0.01, -0.0075, -0.005, -0.0025, 0, 0.0025, 0.005, 0.0075, 0.01], "DCF!$B$29", PCT,
               lambda w, c: f"DCF!${L}$10*(1+{c})*(1-{c}/DCF!$B$31)/({w}-{c})/(1+{w})^DCF!${L}$23")
    grid_block(15, "Exit multiple: value per share (₹) - WACC vs EV/EBITDA", "Exit EV/EBITDA",
               [-3, -2, -1, -0.5, 0, 0.5, 1, 2, 3], "DCF!$B$30", MULT,
               lambda w, c: f"DCF!${L}$12*{c}/(1+{w})^DCF!$B$32")
    _w(ws, "A25", f"Colour scale centred on the market price ₹{price:,.2f}: blue = above, red = below.", F_NOTE)

    # Margin x growth grid needs a full re-forecast -> Python engine output
    top = 27
    _sec(ws, top, "[Python] Blended value per share (₹) - EBIT margin shift vs revenue growth shift", 9)
    _w(ws, f"A{top + 1}", "Margin shift ↓ / Growth shift →", F_NOTE)
    mg = margin_growth_grid
    for j, cv in enumerate(mg.columns):
        _w(ws, f"{col(2 + j)}{top + 1}", float(cv), F_BOLD, '+0.0%;-0.0%;0.0%', align="center", fill=FILL_SEC)
    for i, rv in enumerate(mg.index):
        _w(ws, f"A{top + 2 + i}", float(rv), F_BOLD, '+0.0%;-0.0%;0.0%', align="center", fill=FILL_SEC)
        for j in range(mg.shape[1]):
            _w(ws, f"{col(2 + j)}{top + 2 + i}", float(mg.iloc[i, j]), F_CALC, RS, border=BOX, align="center")
    rng = f"B{top + 2}:{col(1 + mg.shape[1])}{top + 1 + mg.shape[0]}"
    ws.conditional_formatting.add(rng, ColorScaleRule(start_type="min", start_color="F8A5A4", mid_type="num",
                                                      mid_value=price, mid_color="F0EFEC", end_type="max", end_color="86B6EF"))
    if "tornado" in charts:
        img = XLImage(str(charts["tornado"]))
        img.width, img.height = img.width * 0.42, img.height * 0.42
        ws.add_image(img, f"A{top + 4 + mg.shape[0]}")

    # ======================= SCENARIOS =======================
    ws = ws_sc
    _title(ws, "[Python] Scenario analysis", "Engine output for all three scenarios side by side. To see any one "
           "of them flow through the live model, set Inputs!B42.")
    ws.column_dimensions["A"].width = 30
    for c_ in "BCD":
        ws.column_dimensions[c_].width = 34
    _hdr(ws, 4, ["Metric"] + list(scen_df["Scenario"]))
    metrics = [("Revenue CAGR (10y)", PCT), ("Avg EBIT margin", PCT), ("WACC", PCT2), ("Terminal growth", PCT),
               ("Exit EV/EBITDA", MULT), ("Value / share (Gordon)", RS), ("Value / share (Exit)", RS),
               ("Value / share (Blended)", RS), ("Upside vs price", PCT)]
    for i, (m, fmt) in enumerate(metrics):
        r = 5 + i
        _w(ws, f"A{r}", m, F_BOLD if m == "Value / share (Blended)" else F_LBL)
        for j, key in enumerate(scen_df.index):
            _w(ws, f"{col(2 + j)}{r}", float(scen_df.loc[key, m]), F_CALC_B if "Blended" in m else F_CALC, fmt,
               align="center", fill=FILL_OUT if "Blended" in m else None)
    narratives = {
        "bear": "Gen-AI compresses managed-services pricing faster than new AI work replaces it; CC growth stalls "
                "near 0-1%, margins slip toward 19%, and the market demands a higher return.",
        "base": "Growth tracks guidance near-term (1-2% CC) and recovers to mid-single digit CC as AI services "
                "scale; margins hold in the 20.5-21% band.",
        "bull": "AI revenue (+29% QoQ in Q2 FY27) becomes a growth engine, large-deal TCV converts, CC growth "
                "returns to 6-7%, and margins edge up on automation-led productivity.",
    }
    _w(ws, "A15", "Narrative", F_BOLD)
    for j, key in enumerate(scen_df.index):
        c = _w(ws, f"{col(2 + j)}15", narratives.get(key, ""), F_LBL)
        c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[15].height = 95
    if "scenarios" in charts:
        img = XLImage(str(charts["scenarios"]))
        img.width, img.height = img.width * 0.45, img.height * 0.45
        ws.add_image(img, "A17")

    # ======================= MONTE CARLO =======================
    ws = ws_mc
    st = mc.stats
    _title(ws, "[Python] Monte Carlo simulation", f"{st['simulations']:,} correlated draws (Gaussian copula via "
           "Cholesky) of growth, margin, WACC, terminal growth and exit multiple. Blended value per share.")
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["D"].width = 30
    ws.column_dimensions["E"].width = 16
    _hdr(ws, 4, ["Statistic", "Value"])
    srows = [("Mean", st["mean"], RS), ("Standard deviation", st["std"], RS), ("P5", st["p5"], RS),
             ("P10", st["p10"], RS), ("P25", st["p25"], RS), ("Median (P50)", st["p50"], RS),
             ("P75", st["p75"], RS), ("P90", st["p90"], RS), ("P95", st["p95"], RS),
             ("Market price", st["price"], RS2), ("P(value > price)", st["prob_above_price"], PCT),
             ("P(value > price + 20%)", st["prob_20pct_upside"], PCT),
             ("P(value < price - 20%)", st["prob_20pct_downside"], PCT)]
    for i, (lab, v, fmt) in enumerate(srows):
        _w(ws, f"A{5 + i}", lab, F_LBL)
        _w(ws, f"B{5 + i}", float(v), F_CALC_B if "P(" in lab else F_CALC, fmt,
           fill=FILL_OUT if lab == "P(value > price)" else None)
    _hdr(ws, 4, ["Driver (Spearman rank corr. with value)", "Correlation"], start_col=4)
    for i, (k, v) in enumerate(mc.driver_correlations.items()):
        _w(ws, f"D{5 + i}", k, F_LBL)
        _w(ws, f"E{5 + i}", float(v), F_CALC, '+0.00;-0.00')
    mcc = cfg["monte_carlo"]
    _hdr(ws, 12, ["Distribution assumptions", ""], start_col=4)
    dist = [("Growth shift ~ N(0, σ)", mcc["growth_shift_sd"], PCT), ("Margin shift ~ N(0, σ)", mcc["margin_shift_sd"], PCT),
            ("WACC ~ N(base, σ)", mcc["wacc_sd"], PCT2), ("Terminal g ~ Tri(low)", mcc["terminal_growth"]["low"], PCT),
            ("Terminal g ~ Tri(mode)", mcc["terminal_growth"]["mode"], PCT), ("Terminal g ~ Tri(high)", mcc["terminal_growth"]["high"], PCT),
            ("Exit multiple ~ N(base, σ)", mcc["exit_multiple_sd"], MULT), ("ρ growth-margin", mcc["correlation"]["growth_margin"], DEC2),
            ("ρ WACC-terminal g", mcc["correlation"]["wacc_terminal_growth"], DEC2), ("ρ growth-exit multiple", mcc["correlation"]["growth_exit_multiple"], DEC2)]
    for i, (lab, v, fmt) in enumerate(dist):
        _w(ws, f"D{13 + i}", lab, F_LBL)
        _w(ws, f"E{13 + i}", v, F_IN, fmt)
    if "mc" in charts:
        img = XLImage(str(charts["mc"]))
        img.width, img.height = img.width * 0.42, img.height * 0.42
        ws.add_image(img, "A25")

    # ======================= COMPS =======================
    ws = ws_c
    _title(ws, "Trading comparables - Indian Tier-1 IT services", "Peer multiples are inputs (snapshot); implied "
           "Infosys values are live formulas.")
    ws.column_dimensions["A"].width = 34
    for c_ in "BCD":
        ws.column_dimensions[c_].width = 16
    _hdr(ws, 4, ["Peer", "Ticker", "P/E (TTM)", "EV/EBITDA (TTM)"])
    peers = cfg["comps"]["peers"]
    for i, p in enumerate(peers):
        r = 5 + i
        _w(ws, f"A{r}", p["name"], F_LBL)
        _w(ws, f"B{r}", p["ticker"], F_LBL)
        _w(ws, f"C{r}", p["pe_ttm"], F_IN, MULT)
        if p["ev_ebitda_ttm"] is not None:
            _w(ws, f"D{r}", p["ev_ebitda_ttm"], F_IN, MULT)
        else:
            _w(ws, f"D{r}", "n/a", F_NOTE, align="right")
    last = 4 + len(peers)
    stats_rows = [("25th percentile", "QUARTILE({r},1)"), ("Median", "MEDIAN({r})"), ("75th percentile", "QUARTILE({r},3)")]
    for k, (lab, fx) in enumerate(stats_rows):
        r = last + 2 + k
        _w(ws, f"A{r}", lab, F_BOLD)
        _w(ws, f"C{r}", "=" + fx.format(r=f"C5:C{last}"), F_CALC_B, MULT)
        _w(ws, f"D{r}", "=" + fx.format(r=f"D5:D{last}"), F_CALC_B, MULT)
    q1 = last + 2
    tt = snap["ttm_inr_cr"]
    base = last + 7
    _sec(ws, base - 1, "Infosys TTM metrics (to 30-Sep-2026)", 4)
    trows = [
        (base, "TTM revenue (₹ crore)", tt["revenue"], NUM, F_IN, tt["revenue_note"]),
        (base + 1, "TTM net income (₹ crore)", tt["net_income"], NUM, F_IN, tt["net_income_note"]),
        (base + 2, "TTM EBITDA margin (estimate)", cfg["comps"]["infosys_ttm_ebitda_margin"], PCT, F_IN, "EBIT 21.0% + D&A 2.7%"),
        (base + 3, "TTM EBITDA (₹ crore)", f"=B{base}*B{base + 2}", NUM, F_CALC, ""),
        (base + 4, "TTM EPS (₹)", f"=B{base + 1}/Inputs!B8", RS2, F_CALC, ""),
        (base + 5, "Net cash & other claims (₹ crore)", "=Inputs!B9-Inputs!B10-Inputs!B11-Inputs!B12", NUM, F_LINK, ""),
        (base + 6, "Infosys current P/E", f"=Inputs!B7/B{base + 4}", MULT, F_CALC, ""),
        (base + 7, "Infosys current EV/EBITDA", f"=(Inputs!B7*Inputs!B8-B{base + 5})/B{base + 3}", MULT, F_CALC, ""),
    ]
    for r, lab, v, fmt, font, note in trows:
        _w(ws, f"A{r}", lab, F_LBL)
        _w(ws, f"B{r}", v, font, fmt)
        if note:
            _note(ws, f"B{r}", note)
    imp = base + 9
    _hdr(ws, imp, ["Implied value per share (₹)", "25th pct", "Median", "75th pct"])
    _w(ws, f"A{imp + 1}", "P/E x TTM EPS", F_LBL)
    _w(ws, f"A{imp + 2}", "EV/EBITDA x TTM EBITDA + net cash", F_LBL)
    for k in range(3):
        c_ = col(2 + k)
        _w(ws, f"{c_}{imp + 1}", f"=C{q1 + k}*$B${base + 4}", F_CALC_B, RS, align="center")
        _w(ws, f"{c_}{imp + 2}", f"=(D{q1 + k}*$B${base + 3}+$B${base + 5})/Inputs!$B$8", F_CALC_B, RS, align="center")
    _w(ws, f"A{imp + 4}", "Sources: " + " ".join(cfg["comps"]["sources"].split()), F_NOTE)
    comps_rows = {"pe": imp + 1, "ev": imp + 2}

    # ======================= FOOTBALL FIELD =======================
    ws = ws_ff
    _title(ws, "Valuation summary - football field", "Low / high value per share by method. Live where the source "
           "is a formula; [Python] rows are engine outputs.")
    ws.column_dimensions["A"].width = 44
    for c_ in "BCD":
        ws.column_dimensions[c_].width = 14
    _hdr(ws, 4, ["Method", "Low (₹)", "High (₹)", "Source"])
    sb = scen_df
    ff_rows = [
        ("DCF - Value driver (WACC ±1%, g ±0.5%)", "=MIN(Sensitivity!D7:H11)", "=MAX(Sensitivity!D7:H11)", "Live", F_LINK),
        ("DCF - Exit multiple (WACC ±1%, multiple ±1.0x)", "=MIN(Sensitivity!D18:H22)", "=MAX(Sensitivity!D18:H22)", "Live", F_LINK),
        ("DCF - Scenarios (Bear to Bull, blended)", float(sb.loc["bear", "Value / share (Blended)"]),
         float(sb.loc["bull", "Value / share (Blended)"]), "[Python]", F_CALC),
        ("Monte Carlo (P10 to P90)", float(st["p10"]), float(st["p90"]), "[Python]", F_CALC),
        ("Trading comps - P/E (IQR)", f"=Comps!B{comps_rows['pe']}", f"=Comps!D{comps_rows['pe']}", "Live", F_LINK),
        ("Trading comps - EV/EBITDA (IQR)", f"=Comps!B{comps_rows['ev']}", f"=Comps!D{comps_rows['ev']}", "Live", F_LINK),
        ("52-week trading range", snap["market"]["week52_low"], snap["market"]["week52_high"], "Market", F_IN),
    ]
    for i, (lab, lo, hi, src, font) in enumerate(ff_rows):
        r = 5 + i
        _w(ws, f"A{r}", lab, F_LBL)
        _w(ws, f"B{r}", lo, font, RS, align="center")
        _w(ws, f"C{r}", hi, font, RS, align="center")
        _w(ws, f"D{r}", src, F_NOTE, align="center")
    _w(ws, "A13", "Market price", F_BOLD)
    _w(ws, "B13", "=Inputs!B7", F_LINK, RS2, align="center")
    _w(ws, "A14", "Headline intrinsic value (DCF blend)", F_BOLD)
    _w(ws, "B14", "=DCF!B56", F_LINK, RS, fill=FILL_KEY, align="center")
    if "football" in charts:
        img = XLImage(str(charts["football"]))
        img.width, img.height = img.width * 0.45, img.height * 0.45
        ws.add_image(img, "A16")

    # ======================= COVER =======================
    ws = ws_cover
    _title(ws, f"{cfg['company']['name']} (NSE: INFY) - DCF Valuation",
           f"Valuation date {inputs.valuation_date:%d %b %Y}  |  INR crore unless stated  |  Not investment advice")
    ws.column_dimensions["A"].width = 44
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 4
    ws.column_dimensions["D"].width = 44
    ws.column_dimensions["E"].width = 18
    _hdr(ws, 4, ["Valuation output", "Live value"])
    out = [
        ("Active scenario", "=Inputs!C42", None, F_LINK),
        ("Headline intrinsic value per share", "=DCF!B56", RS, F_LINK),
        ("Market price", "=Inputs!B7", RS2, F_LINK),
        ("Upside / (downside)", "=DCF!B57", PCT, F_LINK),
        ("Value per share - Gordon growth", "=DCF!B51", RS, F_LINK),
        ("Value per share - Value driver", "=DCF!C51", RS, F_LINK),
        ("Value per share - Exit multiple", "=DCF!D51", RS, F_LINK),
        ("Enterprise value (Gordon, ₹ crore)", "=DCF!B44", NUM, F_LINK),
        ("WACC", "=WACC!B23", PCT2, F_LINK),
        ("Cost of equity", "=WACC!B10", PCT2, F_LINK),
        ("Terminal growth", "=DCF!B29", PCT, F_LINK),
        ("Terminal value % of EV (Gordon)", "=DCF!B53", PCT, F_LINK),
        ("Implied exit multiple from Gordon TV", "=DCF!B58", MULT, F_LINK),
    ]
    for i, (lab, v, fmt, font) in enumerate(out):
        r = 5 + i
        _w(ws, f"A{r}", lab, F_BOLD if i in (1, 3) else F_LBL)
        _w(ws, f"B{r}", v, font, fmt, fill=FILL_KEY if i == 1 else None, align="right")
    _hdr(ws, 4, ["[Python] Engine outputs", "Value"], start_col=4)
    py = [
        ("Market-implied 10y revenue CAGR (reverse DCF)", reverse["implied_revenue_cagr"], PCT),
        ("Base-case 10y revenue CAGR", reverse["base_revenue_cagr"], PCT),
        ("Market-implied WACC (reverse DCF)", reverse["implied_wacc"], PCT2),
        ("Monte Carlo median value", st["p50"], RS),
        ("Monte Carlo P10 - P90", f"₹{st['p10']:,.0f} - ₹{st['p90']:,.0f}", None),
        ("P(intrinsic value > price)", st["prob_above_price"], PCT),
        ("Bear / Base / Bull (blended)", " / ".join(f"₹{v:,.0f}" for v in scen_df["Value / share (Blended)"]), None),
    ]
    for i, (lab, v, fmt) in enumerate(py):
        r = 5 + i
        _w(ws, f"D{r}", lab, F_LBL)
        _w(ws, f"E{r}", v if isinstance(v, str) else float(v), F_CALC, fmt, align="right")
    _sec(ws, 19, "How to use this workbook", 5)
    guide = [
        "1. Change any blue cell on 'Inputs' (yellow = key levers). DCF, WACC, Sensitivity, Comps and this Cover re-price live.",
        "2. Flip Inputs!B42 between 1 / 2 / 3 to run the Bear / Base / Bull case through the live model.",
        "3. Tabs marked [Python] (Scenarios, Monte Carlo, margin x growth grid) come from the Python engine: "
        "edit config/infosys.yaml and run `python run_valuation.py`.",
        "4. Colour code: blue = input, black = formula, green = link to another sheet, yellow fill = key lever.",
    ]
    for i, g in enumerate(guide):
        _w(ws, f"A{20 + i}", g, F_LBL)
    if "football" in charts:
        img = XLImage(str(charts["football"]))
        img.width, img.height = img.width * 0.42, img.height * 0.42
        ws.add_image(img, "A26")

    for wsx in wb.worksheets:
        wsx.sheet_properties.tabColor = NAVY if wsx.title in ("Cover", "Inputs") else (
            "70AD47" if wsx.title in ("DCF", "WACC", "Sensitivity", "Comps", "Football Field") else "A6A6A6")
    wb.save(path)
    return path
