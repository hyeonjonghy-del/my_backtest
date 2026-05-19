from __future__ import annotations

import numpy as np
import pandas as pd
from matplotlib import pyplot as plt


DEFAULT_COLORS = [
    "#0F766E",
    "#2563EB",
    "#DC2626",
    "#7C3AED",
    "#F59E0B",
    "#111827",
    "#64748B",
]


def _chart_data(data: pd.DataFrame, max_points: int = 900) -> pd.DataFrame:
    clean = data.replace([np.inf, -np.inf], np.nan).dropna(how="all")
    if len(clean) <= max_points:
        return clean
    step = int(np.ceil(len(clean) / max_points))
    return clean.iloc[::step].copy()


def static_line_chart(
    data: pd.DataFrame,
    title: str,
    yaxis_title: str = "",
    percent_axis: bool = False,
    height: int = 340,
    mdd_info: dict[str, object] | None = None,
):
    clean = _chart_data(data)
    fig, ax = plt.subplots(figsize=(11, max(height / 100, 2.8)), dpi=120)

    for i, column in enumerate(clean.columns):
        series = clean[column].dropna()
        ax.plot(
            series.index,
            series.values,
            label=str(column),
            color=DEFAULT_COLORS[i % len(DEFAULT_COLORS)],
            linewidth=2.0 if i == 0 else 1.35,
        )

    if mdd_info is not None:
        ax.plot(
            [mdd_info["peak_date"], mdd_info["date"]],
            [mdd_info["peak_value"], mdd_info["trough_value"]],
            color="#B91C1C",
            linestyle=":",
            linewidth=1.8,
            marker="o",
            label=f"MDD {mdd_info['value']:.1%}",
        )
        ax.annotate(
            f"MDD {mdd_info['value']:.1%}",
            xy=(mdd_info["date"], mdd_info["trough_value"]),
            xytext=(18, 28),
            textcoords="offset points",
            arrowprops={"arrowstyle": "->", "color": "#B91C1C"},
            color="#B91C1C",
            fontsize=10,
        )

    ax.set_title(title, loc="left", fontsize=13, fontweight="bold")
    ax.set_ylabel(yaxis_title)
    ax.grid(True, axis="y", color="#E5E7EB", linewidth=0.8)
    ax.grid(False, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left", ncols=min(len(clean.columns), 4), frameon=False)
    if percent_axis:
        ax.yaxis.set_major_formatter(lambda x, _: f"{x:.0f}%")
    fig.autofmt_xdate(rotation=0)
    fig.tight_layout()
    return fig


def static_area_chart(data: pd.DataFrame, title: str, height: int = 300):
    clean = _chart_data(data)
    fig, ax = plt.subplots(figsize=(11, max(height / 100, 2.6)), dpi=120)
    columns = list(clean.columns)
    values = [clean[column].fillna(0).values for column in columns]
    ax.stackplot(
        clean.index,
        values,
        labels=columns,
        colors=DEFAULT_COLORS[: len(columns)],
        alpha=0.78,
    )
    ax.set_title(title, loc="left", fontsize=13, fontweight="bold")
    ax.set_ylim(0, max(1.0, float(clean.sum(axis=1).max()) if not clean.empty else 1.0))
    ax.yaxis.set_major_formatter(lambda x, _: f"{x:.0%}")
    ax.grid(True, axis="y", color="#E5E7EB", linewidth=0.8)
    ax.grid(False, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left", ncols=min(len(columns), 4), frameon=False)
    fig.autofmt_xdate(rotation=0)
    fig.tight_layout()
    return fig


def position_action_label(order_abs_sum: float, tolerance: float = 1e-6) -> str:
    return "기존보유비중 유지" if abs(order_abs_sum) <= tolerance else "포지션 및 보유비중 변경"
