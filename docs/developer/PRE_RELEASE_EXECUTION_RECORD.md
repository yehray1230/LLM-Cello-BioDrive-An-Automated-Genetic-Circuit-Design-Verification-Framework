# Pre-release execution record

Date: 2026-07-25
Base revision: `f8ebe00b192db14ca5aa1f7467b00ec4790abf5b`
Release posture: `0.x research preview`
Current decision: **Ready for Review is authorized when GitHub Actions passes
on the exact head containing this final record**

This record reports local computational and software-engineering evidence. It
does not establish biological validity, wet-lab performance, external Cello
mapping, formal confirmatory holdout acceptance, or production readiness.

## Scope and hygiene

- Staging decisions are recorded in
  [`PRE_RELEASE_STAGING_MANIFEST.md`](PRE_RELEASE_STAGING_MANIFEST.md).
- `.codex_test_logs/`, coverage data, pytest temp directories, and Mutmut
  working files are ignored.
- Candidate-file scan found no file larger than 1 MB, unexpected environment or
  binary directories, private-key headers, known token formats, or generic
  secret assignments.
- The final tracked/untracked candidate secret scan inspected 437 readable files
  and found zero known secret-pattern hits.
- Twenty-eight candidate Markdown files exposed 105 relative links; zero were
  broken at the time of the scan.
- Candidate JSON parsed cleanly; text files were valid UTF-8 without NUL bytes.
- Third-party notices now identify pytest-cov and Mutmut as non-runtime
  development tools. Mutmut is platform-marked off native Windows.

## Automated verification

| Check | Result |
| --- | --- |
| Registry freshness | PASS |
| Import-patch verification | PASS |
| `llms-full.txt` two-pass regeneration | PASS; identical SHA-256 `16fc1cbec95b885db5a4a775d1f149e95fdb87fbc72100c97320766740e4cd8b` |
| Ruff | PASS |
| `git diff --check` | PASS; Windows line-ending notices only |
| Focused test groups | PASS |
| Final Windows full pytest with coverage | PASS; 1,145 passed, one explained Starlette/httpx deprecation warning |
| Latest Linux PR full pytest | PASS; 1,141 passed, 4 platform-dependent skips, one explained warning |
| Targeted Linux mutation | PASS; 310 generated, 297 killed, 13 reviewed survivors, 95.81% score |
| Evidence Governance public proof | PASS |
| Mypy | NON-BLOCKING BASELINE; 484 errors in 106 files, not an existing CI gate |

Focused groups totalled 469 contract, 47 resource, 63
export/adapter/MCP/safety, 93 EGMA/EXP-011, and 25 publication tests. These
groups overlap; 1,134 is the authoritative unique full-suite count.

Coverage details are recorded in
[`PRE_RELEASE_QUALITY_BASELINE.md`](PRE_RELEASE_QUALITY_BASELINE.md).

## Manual Web QA

The FastAPI/HTML application was served locally on `127.0.0.1:8000`. No API
key, external model provider, paid service, external Cello instance, or wet-lab
system was used.

### Page and endpoint smoke

The following returned HTTP 200 and rendered the expected primary surface:

- `/web`
- `/web/runs`
- `/web/research`
- `/web/benchmarks`
- `/web/imports`
- `/web/assembly`
- `/web/designs`
- `/web/resource-calibrations`
- `/web/compare`
- `/web/settings`
- `/docs`
- `/api/v1/health`
- `/api/v2/health`

Across the HTML pages checked:

- expected `h1`/Swagger surfaces were present;
- no broken images were observed;
- no visible traceback or local `C:\Users\...` path was found;
- browser console warning/error collection was empty.

### Interaction and responsive checks

| Scenario | Result |
| --- | --- |
| 1366×768 dashboard | PASS; navigation and content visible, no horizontal overflow |
| 390×844 dashboard | PASS; responsive stacking, no horizontal overflow |
| Chinese → English | PASS; `lang=en`, heading changed to `Research Dashboard` |
| Browser back/forward | PASS; language and URL state restored correctly |
| Empty structured-intake step | PASS; remained on page, invalid field focused, no run created |
| Model credential preflight | PASS; missing key and offline Cello fallback were visibly labelled |

The narrow layout places the full navigation before dashboard content, which is
usable but vertically long. This is accepted as a P2 usability limitation, not
a release blocker.

### Export and blocker paths

For the persisted conceptual fixture:

| Export | Result |
| --- | --- |
| CSV BOM | 200 |
| SBOL3 Turtle | 200 |
| JSON | 200 |
| Project ZIP | 200 |
| GenBank | 409 blocker |
| Verilog | 409 blocker |
| Plasmid GenBank | 409 blocker |

The 409 responses are expected fail-closed paths for unavailable prerequisites;
they are not counted as successful exports.

### QA findings repaired

1. **Invalid Web run ID returned 500.** `run_detail` now converts the service's
   validation failure to a sanitized HTTP 400 response. A regression test
   verifies the structured error and absence of traceback/local path text.
2. **Share summary overclaimed validation.** Static “Verified”, Cello/UCF
   matching, citation, and wet-lab protocol claims were removed. The summary now
   labels computational checks as non-experimental, states that external
   provenance is required, and explicitly says the research preview is not
   wet-lab validated or an executable protocol. Regression assertions prevent
   the removed claims from returning.

The repaired share summary was rechecked in the browser: both boundary messages
were visible; the former overclaim and static protocol were absent; no API-key
pattern, local path, traceback, or console error was observed.

## Scientific evidence and claim audit

The reproducible Case 01 proof gate reported:

- 4 of 6 evidence records available;
- `computationally_consistent`: supported;
- `sequence_supported`: limited;
- `externally_mapped`: unsupported;
- `experimentally_supported`: unsupported;
- overall license decision: `attribution_required`.

Passing this proof means recorded decisions reproduce from manifest inputs. It
does not mean every biological claim is supported.

The validated-circuit dataset remains a literature-curated engineering fixture.
Its project-authored structure/annotation license scope is explicit, while
source rights and parameter review remain pending. It is not eligible for
evidence promotion.

## Reproducibility repairs

- Case-study GenBank generation now receives an explicit record date.
- The committed case-study output and a fresh generation matched byte for byte
  across all 10 artifacts.
- The dataset metadata version is 1.2.1 with conservative source-rights and
  claim limitations.

## Coverage, mutation, and Gherkin

- Final Windows statement coverage: 83.31%.
- Final Windows branch coverage: 67.95%.
- Latest Linux PR statement / branch coverage: 82.64% / 67.31%.
- `src/utils/safety_checker.py`: 97.39% statement and 96.67% branch coverage
  on both final environments.
- Native Windows mutation execution: not supported by Mutmut 3; this host's WSL
  enumeration is access-denied.
- Linux targeted mutation workflow: PASS on
  `629821754c4c78c9add2b6d78db9cce2488f83f3`.
- Mutation result: 310 generated, 297 killed, 13 survived, 0 timeout,
  suspicious, skipped, or untested; score 95.81%.
- Survivor review: 6 benign-empty sentinel equivalents, 4 encoding
  alias/runner-default limitations, and 3 serialization or temporary-path
  formatting equivalents. No survivor changes a safety decision, fail-closed
  flag, review requirement, export blocker, audit field, redaction boundary,
  error channel, or UTC timestamp.
- Gherkin: intentionally not introduced; existing pytest scenarios and manual
  claim matrix remain authoritative until a product-owner-readable pilot has a
  stable workflow to justify it.

## Provider, cost, and external-validation boundary

- Provider calls: 0.
- Paid cost: USD 0.
- External Cello runs: 0.
- Formal holdout runs: 0.
- Wet-lab runs: 0.

Any future paid EXP-011 or formal holdout action requires a fresh, named
authorization and separate evidence freeze. This release work does not grant
that authority.

## Gate decision

The code/test head satisfies the Draft and Ready evidence gates:

1. all partitioned commits are pushed to Draft PR #16;
2. both Linux CI events passed on the reviewed code/test head;
3. targeted mutation completed and every survivor is classified;
4. local regression, coverage, manual QA, secret, provenance, and claim
   boundary checks pass;
5. provider calls, paid cost, external Cello runs, formal holdout runs, and
   wet-lab runs remain zero.

The documentation-only commit containing this record must pass GitHub Actions
on its exact head before the PR is marked Ready. Merge is permitted only after
that final head is green, the PR is mergeable, the PR body matches this record,
and no unresolved review blocker exists.
