# Pre-release quality baseline

Date: 2026-07-25
Scope: working tree based on `f8ebe00b192db14ca5aa1f7467b00ec4790abf5b`

This record supplements
[`PRE_RELEASE_TEST_AND_PUBLISH_PLAN.md`](PRE_RELEASE_TEST_AND_PUBLISH_PLAN.md).
It records observed engineering evidence only. It is not biological,
wet-lab, external-mapping, or production validation.

## Automated regression evidence

| Gate | Result | Evidence |
| --- | --- | --- |
| Focused contract tests | PASS | 469 passed |
| Focused resource-model tests | PASS | 47 passed |
| Focused export, adapter, MCP, and safety tests | PASS | 63 passed |
| Focused EGMA and EXP-011 tests | PASS | 93 passed |
| Focused publication tests | PASS | 25 passed |
| Full pytest without coverage | PASS | 1,133 passed before QA fixes; one explained Starlette/httpx deprecation warning |
| Full pytest with branch coverage | PASS | 1,134 passed after QA fixes; the same explained warning |
| Ruff | PASS | No findings |
| Generated registry check | PASS | Registry is current |
| `llms-full.txt` regeneration | PASS | Stable on a second regeneration |
| Import-patch verification | PASS | All checks passed |
| Mypy baseline | NON-BLOCKING DEBT | 484 errors in 106 files with `--explicit-package-bases`; mypy is not an existing CI gate |

Focused-test counts overlap and must not be added to infer a unique test count.
The full-suite result is the authoritative unique regression count.

## Coverage baseline

Command:

```powershell
.\venv\Scripts\python.exe -m pytest -q `
  -p no:cacheprovider `
  --basetemp=tmp_pytest\pre_release_coverage `
  --cov=application `
  --cov=benchmark_suite `
  --cov=src `
  --cov-branch `
  --cov-report=term `
  --cov-report=json:outputs\pre_release\coverage.json
```

Observed totals:

| Metric | Result |
| --- | ---: |
| Statements | 21,753 |
| Covered statements | 18,122 |
| Statement coverage | 83.31% |
| Branches | 7,096 |
| Covered branches | 4,822 |
| Branch coverage | 67.95% |
| Combined coverage.py display | 80% |

The first baseline is informational: CI uploads the JSON report but does not
yet impose a repository-wide `fail-under`. Raising a global branch threshold
from the observed 67.95% is a separate test-improvement task and must not be
represented as already achieved.

High-risk targeted module:

| Module | Statement coverage | Branch coverage | Decision |
| --- | ---: | ---: | --- |
| `src/utils/safety_checker.py` | 97.39% | 96.67% | Exceeds the planned 90% high-risk branch baseline |

The latest Linux PR run reported 1,141 passed and 4 platform-dependent skips.
Its coverage artifact reported 82.64% statement coverage, 67.31% branch
coverage, and 97.39% statement / 96.67% branch coverage for
`src/utils/safety_checker.py`. The Windows exact-head run reported 1,145
passed; the platform difference is the four optional assembly tests skipped on
Linux, not four missing test cases.

## Mutation testing

Mutmut 3.6.0 copies the complete `src` tree and required application runtime
dependencies, mutates only `src/utils/safety_checker.py`, and selects
`tests/test_safety_boundary.py`. The dedicated Linux GitHub Actions workflow
first verifies the focused pytest baseline, runs Mutmut, prints the mutation
results, and uploads both the result text and Mutmut working directory.

Native Windows execution is not supported by Mutmut 3. The local command exits
with the tool's instruction to use WSL. This host cannot enumerate a WSL
distribution (`Wsl/EnumerateDistros/Service/E_ACCESSDENIED`), so no local
mutation score is claimed. The development dependency is therefore
platform-marked to install on non-Windows hosts only.

The final reviewed run on code/test head
`629821754c4c78c9add2b6d78db9cce2488f83f3` generated 310 mutants:

- 297 killed;
- 13 survived;
- 0 timeout, suspicious, skipped, or untested mutants;
- mutation score: 95.81%.

Every survivor was compared to the original function:

| Classification | Count | Reviewed difference |
| --- | ---: | --- |
| Equivalent under the current detector input domain | 6 | Empty optional intent, host, design name, and part fields changed from `""` to the benign sentinel `"XXXX"`; neither value matches any safety pattern or changes the result |
| Encoding alias / runner-default tool limitation | 4 | `utf-8` vs `UTF-8`, or an omitted write encoding on the UTF-8 Linux runner; decoded audit JSON and hashes are unchanged in this run |
| Non-semantic serialization / temporary-path formatting | 3 | `.tmp` vs `.TMP`, omitted JSON indentation, or indentation 2 vs 3; the atomic replacement target and decoded event payload are unchanged |

No surviving mutant changes a safety decision, fail-closed flag, review
requirement, export blocker, audit field, redaction boundary, error channel, or
UTC timestamp contract. Earlier behavioral survivors in those areas were
repaired with focused tests and killed by the final run.

Release decision:

- Targeted mutation gate: PASS with reviewed equivalent/tool-limitation
  survivors.
- Ready for Review: permitted after the exact documentation head also passes
  GitHub Actions.
- No mutation result may be described as biological or scientific validation.

## Gherkin / BDD decision

Gherkin is not a release gate for this change set. The repository currently has
no `.feature` files or Behave/pytest-bdd runner, while the existing pytest suite
already exercises API, workflow, failure, and claim-boundary scenarios.

Introduce a pilot only when a stable user workflow needs product-owner review
in shared Given/When/Then language. The pilot must reuse application fixtures
and must not duplicate large pytest implementations. Until that trigger exists,
pytest scenario tests, the release claim matrix, and manual QA are the
authoritative executable and review artifacts.

## Remaining release gates

- Browser/manual QA completed; see
  [`PRE_RELEASE_EXECUTION_RECORD.md`](PRE_RELEASE_EXECUTION_RECORD.md).
- Staged-scope secret, provenance, and claim audits are complete.
- Draft PR #16 contains the deliberately partitioned commits.
- CI coverage and Linux mutation artifacts are reviewed above.
- The documentation-only final head must pass GitHub Actions before promotion
  to Ready for Review.
