import pandas as pd

from src.mock_data import mock_analysis_rows
from src.presentation import (
    OUTPUT_COLUMNS,
    build_output_dataframe,
    dataframe_to_csv_bytes,
    support_band_style,
)


def test_output_table_has_one_row_per_reference() -> None:
    frame = build_output_dataframe(mock_analysis_rows())
    assert len(frame) == 4
    assert frame["ref 編號"].tolist() == [47, 107, 109, 110]


def test_output_table_has_fixed_method_a_and_b_columns() -> None:
    frame = build_output_dataframe(mock_analysis_rows())
    assert frame.columns.tolist() == OUTPUT_COLUMNS
    assert "方法 A 支持分數" in frame
    assert "方法 A 證據層級" in frame
    assert "方法 B 支持分數" in frame
    assert "方法 B 證據層級" in frame


def test_method_b_columns_are_blank_before_integration() -> None:
    frame = build_output_dataframe(mock_analysis_rows())
    assert frame["方法 B 支持分數"].isna().all()
    assert (frame["方法 B 證據層級"] == "").all()
    assert (frame["方法 B 證據層級"] == "").all()


def test_support_band_colors_are_distinct() -> None:
    supported = support_band_style("支持")
    uncertain = support_band_style("不確定")
    unsupported = support_band_style("不支持")

    assert "#E8F5E9" in supported
    assert "#FFF3E0" in uncertain
    assert "#FDECEC" in unsupported
    assert len({supported, uncertain, unsupported}) == 3
    assert support_band_style("") == ""


def test_csv_export_has_bom_header_and_one_line_per_row() -> None:
    frame = build_output_dataframe(mock_analysis_rows())
    csv_bytes = dataframe_to_csv_bytes(frame)

    assert csv_bytes.startswith(b"\xef\xbb\xbf")
    decoded = csv_bytes.decode("utf-8-sig")
    parsed = pd.read_csv(pd.io.common.StringIO(decoded))
    assert parsed.columns.tolist() == OUTPUT_COLUMNS
    assert len(parsed) == 4

