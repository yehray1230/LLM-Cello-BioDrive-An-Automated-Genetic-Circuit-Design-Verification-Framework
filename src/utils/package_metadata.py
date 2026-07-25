"""Canonical package-metadata accessors for provenance payloads."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def package_version(package: str) -> str:
    """Return the installed version or the existing unavailable sentinel."""
    try:
        return version(package)
    except PackageNotFoundError:
        return "unavailable"
