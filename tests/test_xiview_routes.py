"""Integration tests for the xiVIEW data routes."""
import pytest

from tests.conftest import PREFIX


DATA_PREFIX = f"{PREFIX}/data"


@pytest.mark.integration
class TestGetDatasets:
    def test_returns_200(self, client):
        resp = client.get(f"{DATA_PREFIX}/get_datasets")
        assert resp.status_code == 200

    def test_exact_count_and_entry(self, client):
        data = client.get(f"{DATA_PREFIX}/get_datasets").json()
        # 7 fixture projects + 1 added by TestWriteNewUpload (TEST_ADMIN) in the same session
        assert len(data) == 8
        assert {"project_id": "ECOLI_MZML", "identification_file_name": "mzml_ecoli_dsso.mzid"} in data


@pytest.mark.integration
class TestVisualisations:
    def test_known_project_returns_200(self, client):
        resp = client.get(f"{DATA_PREFIX}/visualisations/ECOLI_MZML")
        assert resp.status_code == 200

    def test_exact_visualisation(self, client):
        data = client.get(f"{DATA_PREFIX}/visualisations/ECOLI_MZML").json()
        assert len(data) == 1
        assert data[0]["filename"] == "mzml-ecoli-dsso-mzid"
        assert data[0]["visualisation"] == "cross-linking"

    def test_unknown_project_returns_empty_list(self, client):
        data = client.get(f"{DATA_PREFIX}/visualisations/DOESNOTEXIST").json()
        assert data == []


@pytest.mark.integration
class TestGetXiviewMatches:
    def test_known_project_returns_200(self, client):
        resp = client.get(f"{DATA_PREFIX}/get_xiview_matches?project=ECOLI_MZML")
        assert resp.status_code == 200

    def test_exact_count_and_columns(self, client):
        data = client.get(f"{DATA_PREFIX}/get_xiview_matches?project=ECOLI_MZML").json()
        assert len(data) == 22
        expected_keys = {"id", "pi1", "pi2", "sc", "ui", "c_mz", "pc_c", "pc_mz", "sp", "sd", "p", "r", "sip", "msi_id", "msi_pc"}
        assert expected_keys == set(data[0].keys())

    def test_all_matches_belong_to_upload_1(self, client):
        data = client.get(f"{DATA_PREFIX}/get_xiview_matches?project=ECOLI_MZML").json()
        assert all(m["ui"] == 1 for m in data)

    def test_all_matches_pass_threshold(self, client):
        data = client.get(f"{DATA_PREFIX}/get_xiview_matches?project=ECOLI_MZML").json()
        assert all(m["p"] is True for m in data)

    def test_unknown_project_returns_empty_data(self, client):
        # No 404 is raised when no uploads found (file param not provided)
        resp = client.get(f"{DATA_PREFIX}/get_xiview_matches?project=DOESNOTEXIST")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_limit_zero_returns_422(self, client):
        # limit has ge=1 constraint
        resp = client.get(f"{DATA_PREFIX}/get_xiview_matches?project=ECOLI_MZML&limit=0")
        assert resp.status_code == 422

    def test_limit_works(self, client):
        resp = client.get(f"{DATA_PREFIX}/get_xiview_matches?project=ECOLI_MZML&limit=2")
        assert resp.status_code == 200
        assert len(resp.json()) == 2


@pytest.mark.integration
class TestGetXiviewPeptides:
    def test_known_project_returns_200(self, client):
        resp = client.get(f"{DATA_PREFIX}/get_xiview_peptides?project=ECOLI_MZML")
        assert resp.status_code == 200

    def test_exact_count_and_columns(self, client):
        data = client.get(f"{DATA_PREFIX}/get_xiview_peptides?project=ECOLI_MZML").json()
        assert len(data) == 38
        expected_keys = {"id", "u_id", "seq", "prt", "pos", "dec", "ls1", "ls2", "m_as", "m_ps", "m_ms", "cl_m"}
        assert expected_keys == set(data[0].keys())

    def test_all_peptides_belong_to_upload_1(self, client):
        data = client.get(f"{DATA_PREFIX}/get_xiview_peptides?project=ECOLI_MZML").json()
        assert all(p["u_id"] == 1 for p in data)

    def test_unknown_project_returns_empty_data(self, client):
        resp = client.get(f"{DATA_PREFIX}/get_xiview_peptides?project=DOESNOTEXIST")
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.integration
class TestGetXiviewProteins:
    def test_known_project_returns_200(self, client):
        resp = client.get(f"{DATA_PREFIX}/get_xiview_proteins?project=ECOLI_MZML")
        assert resp.status_code == 200

    def test_exact_count_and_columns(self, client):
        data = client.get(f"{DATA_PREFIX}/get_xiview_proteins?project=ECOLI_MZML").json()
        assert len(data) == 12
        expected_keys = {"id", "name", "accession", "sequence", "search_id", "description"}
        assert expected_keys == set(data[0].keys())

    def test_known_protein_accession_present(self, client):
        data = client.get(f"{DATA_PREFIX}/get_xiview_proteins?project=ECOLI_MZML").json()
        assert "P0C0V0" in {p["accession"] for p in data}

    def test_all_proteins_belong_to_upload_1(self, client):
        data = client.get(f"{DATA_PREFIX}/get_xiview_proteins?project=ECOLI_MZML").json()
        assert all(p["search_id"] == "1" for p in data)

    def test_unknown_project_returns_empty_data(self, client):
        resp = client.get(f"{DATA_PREFIX}/get_xiview_proteins?project=DOESNOTEXIST")
        assert resp.status_code == 200
        assert resp.json() == []
