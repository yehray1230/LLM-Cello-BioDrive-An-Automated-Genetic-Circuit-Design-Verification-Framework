# ADR-0001: FastAPI Web Is the Default Interface

**Date:** 2026-07-17

**Status:** Accepted

## Context

The repository contains a server-rendered FastAPI/HTML workspace and an older
Streamlit application. Maintaining full feature parity would duplicate UI,
formatting, settings, and workflow behavior across two large surfaces. Current
Quickstart, API, workflow, and architecture documents already direct users to
FastAPI `/web` and mark Streamlit as maintenance-only.

## Decision

1. FastAPI `/web` is the default user interface.
2. JSON API routes and server-rendered Web routes share application services;
   neither surface owns domain logic.
3. New user-facing capabilities are implemented and verified in the Web
   workspace first.
4. `app.py` remains a legacy/maintenance-only backup. It receives compatibility
   or correctness fixes, but it does not require feature parity.
5. Reusable domain, evaluation, migration, settings, and persistence behavior
   must live below both interfaces.

## Consequences

- Browser and route verification should prioritize the FastAPI workspace.
- Legacy duplication is recorded but does not trigger broad Streamlit cleanup.
- A feature that exists only in Streamlit is not part of the default product
  surface unless separately adopted by the Web workspace.
- Streamlit-specific helper extraction is low priority unless it removes domain
  duplication or correctness risk.

## Implementation anchors

- `src/api/main.py`
- `src/api/routes.py`
- `src/web/routes.py`
- `src/web/templates/`
- `src/api/dependencies.py::get_services`
- `app.py` (legacy)
- `QUICKSTART.md`
