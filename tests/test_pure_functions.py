"""Unit tests for pure helper functions. No database required."""
import pytest

from fastapi import HTTPException

from app.routes.shared import build_xiview_cache_key
from app.routes.xiview_xi2 import _parse_resultset_ids, _normalise_resultset_ids
from app.routes.matrix_xi2 import _matrix_rows, _sample_matches


@pytest.mark.unit
class TestBuildXiviewCacheKey:
    def test_with_file(self):
        key = build_xiview_cache_key("matches", "PXD001", "file.mzid")
        assert key == "xiview:matches:PXD001:file.mzid"

    def test_without_file(self):
        key = build_xiview_cache_key("matches", "PXD001")
        assert key == "xiview:matches:PXD001"

    def test_peptides_endpoint(self):
        assert build_xiview_cache_key("peptides", "PXD123") == "xiview:peptides:PXD123"

    def test_proteins_endpoint_with_file(self):
        key = build_xiview_cache_key("proteins", "PXD999", "myfile.mzid")
        assert key == "xiview:proteins:PXD999:myfile.mzid"

    def test_none_file_omits_file_segment(self):
        key = build_xiview_cache_key("matches", "PXD001", None)
        assert key == "xiview:matches:PXD001"


A = "ace83ae4-3a4a-4d09-bd61-df9326345a88"
B = "37a0bea4-2a40-4ebe-b2b8-3a773e46c12a"
C = "9a50ca00-db52-4163-9d57-ae793b212082"


@pytest.mark.unit
class TestParseResultsetIds:
    def test_single_bare_uuid(self):
        assert _parse_resultset_ids([A]) == [A]

    def test_aggregated_with_groups(self):
        assert _parse_resultset_ids([f"{A}~B3.{B}~B2.{C}~B1"]) == [A, B, C]

    def test_aggregated_without_groups(self):
        assert _parse_resultset_ids([f"{A}.{B}"]) == [A, B]

    def test_repeated_params(self):
        assert _parse_resultset_ids([A, f"{B}~g"]) == [A, B]

    def test_duplicates_removed_keeping_order(self):
        assert _parse_resultset_ids([f"{B}~x.{A}.{B}~y"]) == [B, A]

    def test_invalid_uuid(self):
        with pytest.raises(HTTPException) as e:
            _parse_resultset_ids([f"{A}~B1.not-a-uuid"])
        assert e.value.status_code == 400

    def test_normalise_ignores_order_and_groups(self):
        assert _normalise_resultset_ids([f"{C}~B1.{A}~B3"]) == sorted([A, C])


S = "11111111-1111-1111-1111-111111111111"


def _match(i, pep1, pep2, s1, s2, sc=1.0):
    return {"id": f"m{i:03d}", "search_id": S, "pep1_id": pep1, "pep2_id": pep2, "s1": s1, "s2": s2, "sc": sc}


@pytest.mark.unit
class TestMatrixRows:
    def test_unique_positions(self):
        rows = _matrix_rows([_match(1, 1, 2, 3, 5, 9.5)],
                            {(S, 1): [("P1", 10, False)], (S, 2): [("P2", 100, False)]})
        assert rows == [{"Protein1": "P1", "Protein2": "P2", "Score": 9.5, "Decoy1": False,
                         "Decoy2": False, "SeqPos1": "12", "SeqPos2": "104"}]

    def test_ambiguous_peptide_joined_with_semicolons(self):
        rows = _matrix_rows([_match(1, 1, 2, 1, 1)],
                            {(S, 1): [("P1", 10, False), ("P3", 50, False)], (S, 2): [("P2", 1, False)]})
        assert rows[0]["Protein1"] == "P1;P3"
        assert rows[0]["SeqPos1"] == "10;50"

    def test_decoy_if_any_protein_is_decoy(self):
        rows = _matrix_rows([_match(1, 1, 2, 1, 1)],
                            {(S, 1): [("P1", 1, False), ("REV_P1", 1, True)], (S, 2): [("P2", 1, False)]})
        assert rows[0]["Decoy1"] is True
        assert rows[0]["Decoy2"] is False

    def test_match_without_positions_dropped(self):
        assert _matrix_rows([_match(1, 1, 2, 1, 1)], {(S, 1): [("P1", 1, False)]}) == []


@pytest.mark.unit
class TestSampleMatches:
    MATCHES = [_match(i, 1, 2, 1, 1) for i in range(50)]

    def test_no_sample_returns_all(self):
        assert _sample_matches(self.MATCHES, None, None) == self.MATCHES

    def test_sample_larger_than_input_returns_all(self):
        assert _sample_matches(self.MATCHES, 100, 1) == self.MATCHES

    def test_sample_size(self):
        assert len(_sample_matches(self.MATCHES, 10, 1)) == 10

    def test_same_seed_same_subset_regardless_of_input_order(self):
        a = _sample_matches(self.MATCHES, 10, 42)
        b = _sample_matches(list(reversed(self.MATCHES)), 10, 42)
        assert a == b

    def test_different_seed_different_subset(self):
        assert _sample_matches(self.MATCHES, 10, 1) != _sample_matches(self.MATCHES, 10, 2)
