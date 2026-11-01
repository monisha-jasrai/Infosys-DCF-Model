"""Static PNG charts for the README and the Excel workbook (matplotlib, light theme)."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#a8a7a1"
GRID = "#e4e3df"
BLUE = "#2a78d6"
BLUE_LIGHT = "#9ec5f4"
ORANGE = "#eb6834"
NEUTRAL = "#f0efec"
RED = "#e34948"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "xtick.color": INK_2, "ytick.color": INK_2,
    "text.color": INK, "font.family": "DejaVu Sans", "font.size": 10,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "axes.titleweight": "bold", "axes.titlesize": 12, "axes.titlelocation": "left",
})


def _rupee(x: float) -> str:
    return f"₹{x:,.0f}"


def _save(fig, path: Path):
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def football_field(rows: list[tuple[str, float, float]], price: float, path: Path, subtitle: str = ""):
    """rows: (label, low, high) ranges of value per share, drawn top-down."""
    fig, ax = plt.subplots(figsize=(9, 0.55 * len(rows) + 1.6))
    y = np.arange(len(rows))[::-1]
    for yi, (label, lo, hi) in zip(y, rows):
        ax.barh(yi, hi - lo, left=lo, height=0.5, color=BLUE, edgecolor=SURFACE, linewidth=2)
        ax.text(lo - 12, yi, _rupee(lo), va="center", ha="right", fontsize=9, color=INK_2,
                bbox=dict(facecolor=SURFACE, edgecolor="none", pad=1))
        ax.text(hi + 12, yi, _rupee(hi), va="center", ha="left", fontsize=9, color=INK_2)
    ax.axvline(price, color=INK, linewidth=1.5, linestyle=(0, (4, 3)))
    ax.text(price, len(rows) - 0.35, f"Market price {_rupee(price)}", ha="center", va="bottom",
            fontsize=9, color=INK)
    ax.set_yticks(y, [r[0] for r in rows])
    ax.grid(axis="y", visible=False)
    lo_all = min(r[1] for r in rows + [("", price, price)])
    hi_all = max(r[2] for r in rows + [("", price, price)])
    pad = (hi_all - lo_all) * 0.14
    ax.set_xlim(lo_all - pad, hi_all + pad)
    ax.set_ylim(-0.6, len(rows) - 0.1)
    ax.xaxis.set_major_formatter(lambda v, _: _rupee(v))
    ax.set_xlabel("Value per share (INR)")
    ax.set_title("Infosys valuation football field" + (f"\n{subtitle}" if subtitle else ""))
    _save(fig, path)


def mc_histogram(values: np.ndarray, stats: dict, path: Path):
    fig, ax = plt.subplots(figsize=(9, 4.6))
    lo, hi = np.percentile(values, [0.5, 99.5])
    ax.hist(values[(values >= lo) & (values <= hi)], bins=70, color=BLUE, edgecolor=SURFACE, linewidth=0.6)
    ymax = ax.get_ylim()[1]
    for key, lab in (("p10", "P10"), ("p50", "Median"), ("p90", "P90")):
        ax.axvline(stats[key], color=INK_2, linewidth=1, linestyle=(0, (2, 2)))
        ax.text(stats[key], ymax * 0.97, f"{lab}\n{_rupee(stats[key])}", ha="center", va="top",
                fontsize=8.5, color=INK_2, bbox=dict(facecolor=SURFACE, edgecolor="none", pad=1.5))
    ax.axvline(stats["price"], color=INK, linewidth=1.8)
    ax.text(stats["price"], ymax * 0.62, f" Price {_rupee(stats['price'])}\n "
            f"P(value > price) = {stats['prob_above_price']:.0%}", ha="left", va="center", fontsize=9,
            bbox=dict(facecolor=SURFACE, edgecolor=GRID, pad=3))
    ax.xaxis.set_major_formatter(lambda v, _: _rupee(v))
    ax.set_xlabel("Intrinsic value per share (INR)")
    ax.set_ylabel("Simulations")
    ax.set_title(f"Monte Carlo: {stats['simulations']:,} correlated simulations")
    _save(fig, path)


def sensitivity_heatmap(df, price: float, path: Path, row_label: str, col_label: str,
                        row_fmt="{:.1%}", col_fmt="{:.1%}", title: str = ""):
    """Diverging blue (above price) / red (below price) with a neutral midpoint at the price."""
    cmap = LinearSegmentedColormap.from_list("div", [RED, "#f4b3b2", NEUTRAL, BLUE_LIGHT, BLUE])
    vals = df.values.astype(float)
    span = np.nanmax(np.abs(vals - price))
    norm = TwoSlopeNorm(vmin=price - span, vcenter=price, vmax=price + span)
    fig, ax = plt.subplots(figsize=(1.05 * df.shape[1] + 2.2, 0.55 * df.shape[0] + 1.6))
    ax.imshow(vals, cmap=cmap, norm=norm, aspect="auto")
    for i in range(vals.shape[0]):
        for j in range(vals.shape[1]):
            v = vals[i, j]
            if np.isnan(v):
                continue
            ax.text(j, i, f"{v:,.0f}", ha="center", va="center", fontsize=9, color=INK)
    ax.set_xticks(range(df.shape[1]), [col_fmt.format(c) for c in df.columns])
    ax.set_yticks(range(df.shape[0]), [row_fmt.format(r) for r in df.index])
    ax.set_xlabel(col_label)
    ax.set_ylabel(row_label)
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title(title or f"Value per share (INR); blue = above market price {_rupee(price)}, red = below")
    _save(fig, path)


def tornado(df, path: Path):
    base = df.attrs["base"]
    fig, ax = plt.subplots(figsize=(9, 0.5 * len(df) + 1.4))
    y = np.arange(len(df))
    ax.barh(y, df["Low case"] - base, left=base, height=0.55, color=ORANGE, edgecolor=SURFACE,
            linewidth=2, label="Downside")
    ax.barh(y, df["High case"] - base, left=base, height=0.55, color=BLUE, edgecolor=SURFACE,
            linewidth=2, label="Upside")
    for yi, (lo, hi) in enumerate(zip(df["Low case"], df["High case"])):
        ax.text(lo - 8, yi, _rupee(lo), ha="right", va="center", fontsize=8.5, color=INK_2)
        ax.text(hi + 8, yi, _rupee(hi), ha="left", va="center", fontsize=8.5, color=INK_2)
    ax.axvline(base, color=INK, linewidth=1.2)
    ax.set_yticks(y, df["Driver"])
    ax.grid(axis="y", visible=False)
    span = max(base - df["Low case"].min(), df["High case"].max() - base) * 1.25
    ax.set_xlim(base - span, base + span)
    ax.xaxis.set_major_formatter(lambda v, _: _rupee(v))
    ax.set_title(f"What moves the value? One-at-a-time swings around base {_rupee(base)}")
    ax.legend(loc="lower right", frameon=False)
    _save(fig, path)


def forecast_panels(hist, fc, path: Path):
    """Small multiples, one axis each: revenue, EBIT margin, FCFF."""
    hy = list(hist.columns)
    fy = list(fc.columns)
    years = hy + fy
    colors = [MUTED] * len(hy) + [BLUE] * len(fy)
    fig, axes = plt.subplots(1, 3, figsize=(14, 4))
    rev = list(hist.loc["Revenue"]) + list(fc.loc["Revenue"])
    axes[0].bar(years, np.array(rev) / 1000, color=colors, edgecolor=SURFACE, linewidth=1.5)
    axes[0].set_title("Revenue (INR '000 crore)")
    mar = list(hist.loc["EBIT margin"]) + list(fc.loc["EBIT margin"])
    axes[1].plot(years[:len(hy)], mar[:len(hy)], color=MUTED, linewidth=2, marker="o", markersize=5)
    axes[1].plot(years[len(hy) - 1:], mar[len(hy) - 1:], color=BLUE, linewidth=2, marker="o", markersize=5)
    axes[1].set_ylim(0.15, 0.26)
    axes[1].yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    axes[1].set_title("EBIT margin")
    fcf_hist = list(hist.loc["Operating cash flow"] - hist.loc["Capex"] - hist.loc["Lease payments"])
    fcff = fcf_hist + list(fc.loc["FCFF"])
    axes[2].bar(years, np.array(fcff) / 1000, color=colors, edgecolor=SURFACE, linewidth=1.5)
    axes[2].set_title("Free cash flow (INR '000 crore)")
    for ax in axes:
        ax.tick_params(axis="x", rotation=60, labelsize=8)
        ax.grid(axis="x", visible=False)
    fig.text(0.01, -0.06, "Grey = actual (FY22-FY26, FCF = CFO - capex - lease payments). "
             "Blue = forecast (FCFF, unlevered).", fontsize=9, color=INK_2)
    fig.tight_layout()
    _save(fig, path)


def scenario_bars(scen_df, price: float, path: Path):
    fig, ax = plt.subplots(figsize=(8, 4))
    labels = [s.split(" - ")[0] for s in scen_df["Scenario"]]
    vals = scen_df["Value / share (Blended)"].values
    ax.bar(labels, vals, width=0.5, color=BLUE, edgecolor=SURFACE, linewidth=2)
    for i, v in enumerate(vals):
        ax.text(i, v + max(vals) * 0.015, f"{_rupee(v)}\n({v / price - 1:+.0%})", ha="center", va="bottom", fontsize=9)
    ax.axhline(price, color=INK, linewidth=1.5, linestyle=(0, (4, 3)))
    ax.text(len(vals) - 0.5, price, f" Price {_rupee(price)}", va="bottom", ha="right", fontsize=9)
    ax.set_ylim(0, max(vals) * 1.2)
    ax.yaxis.set_major_formatter(lambda v, _: _rupee(v))
    ax.grid(axis="x", visible=False)
    ax.set_title("Blended value per share by scenario")
    _save(fig, path)
