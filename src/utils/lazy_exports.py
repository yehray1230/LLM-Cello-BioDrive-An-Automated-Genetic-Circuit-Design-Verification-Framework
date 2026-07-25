"""Helpers for compatibility-preserving lazy package exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any, Callable, Mapping, MutableMapping


def install_lazy_exports(
    namespace: MutableMapping[str, Any],
    exports: Mapping[str, str],
) -> tuple[Callable[[str], Any], Callable[[], list[str]]]:
    """Build module ``__getattr__`` and ``__dir__`` hooks for an export map."""

    def resolve(name: str) -> Any:
        module_name = exports.get(name)
        if module_name is None:
            raise AttributeError(
                f"module {namespace['__name__']!r} has no attribute {name!r}"
            )
        value = getattr(import_module(module_name), name)
        namespace[name] = value
        return value

    def list_names() -> list[str]:
        return sorted({*namespace, *exports})

    return resolve, list_names
