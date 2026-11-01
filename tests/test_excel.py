"""Excel <-> Python parity. Builds the workbook, recalculates it with LibreOffice (skipped if
LibreOffice is not installed), and checks the spreadsheet formulas reproduce the engine,
including after flipping the scenario switch to Bear."""
import shutil
import subprocess
from pathlib import Path

import pytest
from openpyxl import load_workbook

import run_valuation
from dcf.data import load_inputs
from dcf.model import Drivers, run_dcf
from dcf.wacc import compute_wacc

SOFFICE = shutil.which("soffice") or shutil.which("libreoffice")
pytestmark = pytest.mark.skipif(SOFFICE is None, reason="LibreOffice not installed")


def _recalc(src: Path, outdir: Path) -> Path:
    subprocess.run([SOFFICE, "--headless", "--convert-to", "xlsx", "--outdir", str(outdir), str(src)],
                   check=True, capture_output=True, timeout=180)
    return outdir / src.name


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("xl")
    run_valuation.main(["--out", str(out), "--sims", "2000"])
    return out / "Infosys_DCF_Model.xlsx"


def _engine(scenario):
    inp = load_inputs()
    w = compute_wacc(inp.config["wacc"], inp.market_cap, inp.lease_liabilities)
    return run_dcf(inp, w.wacc, Drivers.from_config(inp.config, scenario))


@pytest.mark.parametrize("scenario,switch", [("base", 2), ("bear", 1), ("bull", 3)])
def test_excel_matches_engine(built, tmp_path, scenario, switch):
    wb = load_workbook(built)
    wb["Inputs"]["B42"] = switch
    src = tmp_path / "in" / built.name
    src.parent.mkdir()
    wb.save(src)
    calc = load_workbook(_recalc(src, tmp_path), data_only=True)
    d = calc["DCF"]
    r = _engine(scenario)
    assert d["B51"].value == pytest.approx(r.per_share["perpetuity"], rel=1e-9)
    assert d["C51"].value == pytest.approx(r.per_share["value_driver"], rel=1e-9)
    assert d["D51"].value == pytest.approx(r.per_share["exit_multiple"], rel=1e-9)
    assert d["B56"].value == pytest.approx(r.headline, rel=1e-9)
    # centre of the live sensitivity grid == the DCF itself
    assert calc["Sensitivity"]["F9"].value == pytest.approx(r.per_share["value_driver"], rel=1e-9)
