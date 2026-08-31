# Public Scope

This repository is a `0.x research preview`. The GitHub-facing surface is
intentionally narrower than the full local research workspace.

## Included in the public result surface

- software implementation and tests;
- architecture, workflow, assumptions, limitations, and evidence-governance
  documentation;
- the deterministic Case 01 evidence and E-BOM governance demonstration;
- a sanitized project closeout describing the historical mapping-only record,
  terminal integration No-Go, zero completed real full-path projects, and
  stopping decision;
- computational behavior described under explicit assumptions.

These materials support claims about software behavior, computational workflow,
provenance, and evidence classification. They do not establish biological
function, wet-lab validation, complete buildability, repeatable external Cello
performance, provider qualification, model superiority, or submission
readiness. The closeout's historical R9 result is one mapping-only case and is
not an integrated or biological result.

## Kept local and excluded from default GitHub staging

The following files are retained in the maintained local workspace for research
continuity but are not treated as public results:

- `benchmark_suite/cello_mapping_v1/`: raw and review-bound experiment packages;
- `benchmark_suite/protocols/exp024_*.json`: local EXP-024 control protocols;
- `cello-run/`: external-tool runtime and run directories;
- `output/`: generated reports, PDFs, and other outputs;
- `preprint/`: manuscript, literature, review, and submission-workspace files;
- `docs/PROJECT_STATE.md`: internal cross-area status and handoff routing.
- `docs/developer/MVP_TEST_PLAN.md`: accumulated internal execution record and
  local-path-sensitive review notes.
- `local_plans_private/`: experiment contracts, ledgers, independent-review
  packets, hashes, and final control records.

The corresponding EXP-024 execution scripts and goal-validator tests are also
local control infrastructure rather than public result evidence.

The public closeout is a manually sanitized summary, not a substitute for the
local immutable records. Public documentation must not link to ignored local
paths or imply that a fresh clone contains the private evidence package.

Public packaging should remove an already tracked local-only path from the Git
index without deleting its local working copy. Before uploading, inspect the
staged diff and use an explicit path allowlist. An ignored path can still be
shared later through a separately reviewed, sanitized public artifact; that
decision requires fresh evidence review. A fresh clone will contain only the
public surface and will not reconstruct the local research workspace.
