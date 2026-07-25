# Duplicate-function quality records

This directory stores the stable input records for the report-only Python
duplicate-function detector.

- `duplication_baseline.json` records group IDs observed in the accepted
  snapshot. It does not approve or classify the underlying candidates.
- `duplication_exceptions.json` records intentional duplicates only after the
  policy fields and a concrete review trigger are supplied.

Run the current comparison from the repository root:

```powershell
python -m src.scripts.check_duplicate_functions
```

Write a detailed machine-readable report without changing the baseline:

```powershell
python -m src.scripts.check_duplicate_functions `
  --output outputs/duplication_report.json
```

Refresh the baseline only after reviewing detector changes and candidate-group
changes:

```powershell
python -m src.scripts.check_duplicate_functions `
  --write-baseline quality/duplication_baseline.json
```

Candidate or new-group findings always exit successfully in the current
report-only phase. Invalid configuration and Python parse errors remain errors
because they make the report incomplete.
