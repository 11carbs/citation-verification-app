import pytest

from src.mock_adapters import MockJevAdapter, MockReferenceLookupAdapter
from src.mock_data import MOCK_CLAIM, MOCK_REFERENCES
from src.pairing import build_citation_pairs
from src.pipeline import run_method_a_pipeline


@pytest.fixture
def paired_mock_rows():
    pairs = build_citation_pairs(MOCK_CLAIM, MOCK_REFERENCES)
    return run_method_a_pipeline(
        pairs,
        MockReferenceLookupAdapter(),
        MockJevAdapter(),
    )

