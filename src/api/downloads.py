"""Shared HTTP download adapters for API and Web routes."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from fastapi import HTTPException
from fastapi.responses import FileResponse


class ArtifactLookup(Protocol):
    def artifact(
        self,
        deliverable_id: str,
        artifact_key: str,
    ) -> tuple[Path, str] | None: ...


def assembly_artifact_file_response(
    artifacts: ArtifactLookup,
    deliverable_id: str,
    artifact_key: str,
) -> FileResponse:
    """Resolve an assembly artifact into the shared HTTP response contract."""
    artifact = artifacts.artifact(deliverable_id, artifact_key)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Assembly artifact not found.")
    path, media_type = artifact
    return FileResponse(path, filename=path.name, media_type=media_type)
