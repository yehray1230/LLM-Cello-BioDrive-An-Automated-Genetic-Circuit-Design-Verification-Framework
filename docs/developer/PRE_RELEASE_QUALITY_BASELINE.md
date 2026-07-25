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
| Covered statements | 18,110 |
| Statement coverage | 83.25% |
| Branches | 7,096 |
| Covered branches | 4,820 |
| Branch coverage | 67.93% |
| Combined coverage.py display | 79% |

The first baseline is informational: CI uploads the JSON report but does not
yet impose a repository-wide `fail-under`. Raising a global branch threshold
from the observed 67.93% is a separate test-improvement task and must not be
represented as already achieved.

High-risk targeted module:

| Module | Statement coverage | Branch coverage | Decision |
| --- | ---: | ---: | --- |
| `src/utils/safety_checker.py` | 91.30% | 90.00% | Meets the planned 90% high-risk branch baseline |

## Mutation testing

Mutmut 3.6.0 is configured to mutate
`src/utils/safety_checker.py` and select
`tests/test_safety_boundary.py`. The dedicated Linux GitHub Actions workflow
first verifies the focused pytest baseline, runs Mutmut, prints the mutation
results, and uploads both the result text and Mutmut working directory.

Native Windows execution is not supported by Mutmut 3. The local command exits
with the tool's instruction to use WSL. This host cannot enumerate a WSL
distribution (`Wsl/EnumerateDistros/Service/E_ACCESSDENIED`), so no local
mutation score is claimed. The development dependency is therefore
platform-marked to install on non-Windows hosts only.

Release decision:

- Draft PR: allowed with mutation marked pending.
- Ready for Review: requires the Linux mutation workflow result to be reviewed.
- Surviving mutants: must be classified as test gaps, equivalent mutants, or
  approved exceptions before the gate is called complete.
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
- Complete the staged-scope secret, provenance, and claim audit.
- Create a deliberately partitioned Draft PR.
- Review CI coverage and Linux mutation artifacts.
- Promote to Ready for Review only when every blocking check is green or has a
  named, written reviewer exception.
