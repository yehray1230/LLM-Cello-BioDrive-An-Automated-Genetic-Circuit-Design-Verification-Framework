from __future__ import annotations

from importlib.metadata import PackageNotFoundError

import pytest

from exporters.plasmid_tools import _package_version as plasmid_package_version
from tools.assembly_planner import _package_version as assembly_package_version
from tools.primer_designer import _package_version as primer_package_version
from utils import package_metadata
from utils.package_metadata import package_version


def test_package_version_consumers_share_canonical_accessor() -> None:
    assert plasmid_package_version is package_version
    assert assembly_package_version is package_version
    assert primer_package_version is package_version


def test_package_version_returns_resolved_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(package_metadata, "version", lambda package: f"{package}-1.2.3")

    assert package_version("example") == "example-1.2.3"


def test_package_version_returns_unavailable_only_when_package_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_package(_: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(package_metadata, "version", missing_package)
    assert package_version("missing") == "unavailable"


def test_package_version_does_not_hide_unexpected_metadata_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_metadata(_: str) -> str:
        raise RuntimeError("metadata backend failed")

    monkeypatch.setattr(package_metadata, "version", broken_metadata)
    with pytest.raises(RuntimeError, match="metadata backend failed"):
        package_version("broken")
