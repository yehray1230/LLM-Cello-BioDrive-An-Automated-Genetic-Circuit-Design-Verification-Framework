"""Application package with compatibility-preserving lazy service exports."""

from __future__ import annotations

from utils.lazy_exports import install_lazy_exports


_EXPORTS = {
    "ApplicationServices": "application.services",
    "create_application_services": "application.services",
    "get_default_services": "application.services",
}

__all__ = list(_EXPORTS)
__getattr__, __dir__ = install_lazy_exports(globals(), _EXPORTS)
