from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from api.downloads import assembly_artifact_file_response


class _ArtifactLookup:
    def __init__(self, artifact: tuple[Path, str] | None) -> None:
        self._artifact = artifact
        self.calls: list[tuple[str, str]] = []

    def artifact(
        self,
        deliverable_id: str,
        artifact_key: str,
    ) -> tuple[Path, str] | None:
        self.calls.append((deliverable_id, artifact_key))
        return self._artifact


def test_assembly_download_preserves_path_filename_and_media_type(
    tmp_path: Path,
) -> None:
    path = tmp_path / "assembly.csv"
    path.write_text("fragment_id\nfragment_1\n", encoding="utf-8")
    lookup = _ArtifactLookup((path, "text/csv"))

    response = assembly_artifact_file_response(
        lookup,
        "deliverable-1",
        "csv",
    )

    assert lookup.calls == [("deliverable-1", "csv")]
    assert Path(response.path) == path
    assert response.filename == "assembly.csv"
    assert response.media_type == "text/csv"


def test_assembly_download_preserves_not_found_contract() -> None:
    lookup = _ArtifactLookup(None)

    with pytest.raises(HTTPException) as captured:
        assembly_artifact_file_response(
            lookup,
            "missing-deliverable",
            "missing-artifact",
        )

    assert captured.value.status_code == 404
    assert captured.value.detail == "Assembly artifact not found."
