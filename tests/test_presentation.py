import pandas as pd

from src.mock_data import mock_analysis_rows
from src.pairing import build_citation_pairs
from src.presentation import (
    OUTPUT_COLUMNS,
    build_output_dataframe,
    dataframe_to_csv_bytes,
    support_band_style,
)
from src.schemas import AnalysisRow


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
    assert (frame["方法 B 判斷依據"] == "").all()
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


def test_csv_preserves_original_sentence_for_separately_cited_clauses() -> None:
    original = (
        "BERT uses bidirectional pre-training [1], "
        "while SciBERT is pretrained on scientific text [2]."
    )
    pairs = build_citation_pairs(original, '[1] "BERT."\n[2] "SciBERT."')
    frame = build_output_dataframe([AnalysisRow(pair) for pair in pairs])
    decoded = dataframe_to_csv_bytes(frame).decode("utf-8-sig")
    exported = pd.read_csv(pd.io.common.StringIO(decoded), keep_default_na=False)

    assert exported["引用陳述"].tolist() == [original, original]
    assert list(zip(exported["ref 編號"], exported["查核子主張"])) == [
        (1, "BERT uses bidirectional pre-training [1]"),
        (2, "SciBERT is pretrained on scientific text [2]."),
    ]
    assert (exported["共引摘要涵蓋"] == "").all()

