from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tools.tool_adapters import (
    SequenceRecordLookupAdapter,
    inspect_capabilities,
)


def test_scientific_capabilities_registered():
    info = inspect_capabilities()
    assert "sequence_record_lookup" in info["capabilities"]
    assert "sequence_record_lookup" in info["catalog"]
    assert "motif_matrix_lookup" in info["catalog"]
    assert "binding_evidence_lookup" in info["catalog"]
    assert "literature_search" in info["catalog"]


def test_sequence_record_lookup_fixture_success():
    adapter = SequenceRecordLookupAdapter()
    result = adapter.run({"accession": "NC_000913.3"})
    assert result.status == "ok"
    assert result.output["provider"] == "ncbi_fixture"
    assert result.output["record"]["accession"] == "NC_000913"
    assert result.output["record"]["protein_translation"] == "MKQATELETR"


def test_sequence_record_lookup_invalid_input():
    adapter = SequenceRecordLookupAdapter()
    result = adapter.run({"accession": 12345})
    assert result.status == "invalid_input"
    assert any(w["code"] == "INVALID_ACCESSION" for w in result.to_dict()["warnings"])


def test_sequence_record_lookup_unresolved_fallback():
    adapter = SequenceRecordLookupAdapter()
    result = adapter.run({"accession": "UNKNOWN_ACCESSION_999"})
    assert result.status == "unresolved"
    assert result.availability.fallback_used is True
    assert any(w["code"] == "RECORD_UNRESOLVED" for w in result.to_dict()["warnings"])
