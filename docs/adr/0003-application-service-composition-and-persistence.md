# ADR-0003: Application Service Composition and Persistence

**Date:** 2026-07-17

**Status:** Accepted

## Context

The application exposes API, Web, MCP, research, benchmark, import, simulation,
optimization, assembly, and export capabilities. Incremental construction of
services inside individual interfaces previously allowed duplicate instances
and inconsistent repositories. Persistence also has distinct ownership for
design revisions, small JSON records, settings, and run lifecycle data.

## Decision

1. `create_application_services()` is the application composition root.
2. `get_services()` is the FastAPI dependency adapter and returns the composed
   service graph; routes do not construct independent service instances.
3. DesignIR v2 repository backend selection belongs to
   `create_design_repository()`, with SQLite as the local default and PostgreSQL
   selected through its documented configuration.
4. `JsonRepository` owns validated, atomic small-record persistence where wired
   by the composition root.
5. `RunStore` owns run metadata, status, events, feedback, results, artifacts,
   and reproducibility manifests.
6. `SettingsService` owns application/Cello settings. Credentials remain behind
   the server boundary and must not be returned in clear text.
7. A new service or repository is instantiated once and shared with dependent
   services through the composition root.

## Consequences

- Tests may override the dependency, but production interfaces share one wiring
  policy.
- Backend-specific code stays behind repository protocols and factories.
- Adding a service requires composition-root and change-impact review.
- Direct JSON/path writes from routes or templates are prohibited.
- Persistence migrations and public API claims remain separate concerns.

## Implementation anchors

- `application/services.py::create_application_services`
- `application/services.py::get_default_services`
- `src/api/dependencies.py::get_services`
- `src/repositories/factory.py::create_design_repository`
- `src/repositories/json_repository.py::JsonRepository`
- `src/repositories/sqlite_repository.py`
- `src/repositories/postgres_repository.py`
- `src/mcp_server/run_store.py::RunStore`
- `application/settings.py::SettingsService`
