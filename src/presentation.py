"""Presentation helpers shared by the Streamlit table and CSV export."""

from __future__ import annotations

import pandas as pd

from src.schemas import AnalysisRow, SupportBand


OUTPUT_COLUMNS = [
    "pair_id",
    "引用陳述",
    "引用標記",
    "ref 編號",
    "被引文獻",
    "ref 配對狀態",
    "錯誤狀態",
    "方法 A 支持分數",
    "方法 A 證據層級",
    "方法 A 暫定分級",
    "方法 B 支持分數",
    "方法 B 證據層級",
    "方法 B 暫定分級",
]

SUPPORT_BAND_COLORS = {
    SupportBand.SUPPORTED.value: ("#E8F5E9", "#1B5E20"),
    SupportBand.UNCERTAIN.value: ("#FFF3E0", "#A65300"),
    SupportBand.UNSUPPORTED.value: ("#FDECEC", "#B42318"),
}


def build_output_dataframe(rows: list[AnalysisRow]) -> pd.DataFrame:
    """Build the stable one-pair-per-row website and CSV table."""

    records = [row.to_record() for row in rows]
    return pd.DataFrame(records, columns=OUTPUT_COLUMNS)


def support_band_style(value: object) -> str:
    """Return accessible background/text colors for a temporary band label."""

    colors = SUPPORT_BAND_COLORS.get(str(value))
    if colors is None:
        return ""
    background, foreground = colors
    return f"background-color: {background}; color: {foreground}; font-weight: 600"


def style_output_dataframe(frame: pd.DataFrame) -> pd.io.formats.style.Styler:
    """Color only the two support-band columns, leaving evidence data intact."""

    band_columns = ["方法 A 暫定分級", "方法 B 暫定分級"]
    return frame.style.map(support_band_style, subset=band_columns)


def dataframe_to_csv_bytes(frame: pd.DataFrame) -> bytes:
    """Export Excel-friendly UTF-8 CSV with a byte-order mark."""

    return frame.to_csv(index=False).encode("utf-8-sig")

