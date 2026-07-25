# Third-Party Notices and License Boundaries

This repository''s original code, documentation, synthetic fixtures, and
project-generated evidence are licensed under Apache-2.0 unless a file or
evidence record says otherwise.

Python packages installed from `requirements*.txt` are separate works supplied
by their respective maintainers. They are not relicensed under Apache-2.0 and
are not vendored in this repository. Redistributors who bundle wheels,
executables, containers, UCFs, biological-part libraries, or datasets must
preserve the corresponding upstream license texts and notices.

This inventory is an engineering compliance aid, not legal advice. Versions and
licenses must be rechecked when dependency locks or bundled artifacts change.

## Direct runtime dependencies

| Dependency | License family observed | Distribution treatment |
| --- | --- | --- |
| beautifulsoup4 | MIT | Separate package; preserve notice if bundled |
| biopython | Biopython License Agreement (BSD-style) | Separate package; preserve upstream license |
| chromadb | Apache-2.0 | Separate package; preserve license and NOTICE if present |
| joblib | BSD-3-Clause | Separate package; preserve notice |
| litellm | MIT | Separate package; preserve notice |
| matplotlib | PSF-based Matplotlib license | Separate package; preserve upstream license |
| numpy | BSD-3-Clause | Separate package; preserve notice |
| pandas | BSD-3-Clause | Separate package; preserve notice |
| pydantic | MIT | Separate package; preserve notice |
| requests | Apache-2.0 | Separate package; preserve license and NOTICE if present |
| scipy | BSD-3-Clause | Separate package; preserve notice |
| sympy | BSD-3-Clause | Separate package; preserve notice |
| streamlit | Apache-2.0 | Separate package; preserve license and NOTICE if present |
| diskcache | Apache-2.0 | Separate package; preserve license and NOTICE if present |
| fastapi | MIT | Separate package; preserve notice |
| jinja2 | BSD-3-Clause | Separate package; preserve notice |
| python-multipart | Apache-2.0 | Separate package; preserve license and NOTICE if present |
| uvicorn | BSD-3-Clause | Separate package; preserve notice |
| psycopg / psycopg-binary | LGPL-3.0 | Database adapter; separate dynamic dependency; preserve LGPL terms and bundled libpq notices |
| pydna | BSD | Separate package used by assembly planning; preserve notice |

## Optional GPL boundary

`primer3-py` is GPL-2.0 and wraps/bundles Primer3 functionality. It is not part
of the Apache-2.0 base installation. It is listed only as an optional
primer-design dependency, and the application must remain importable without
it.

Do not distribute a combined binary, container, or installer containing
`primer3-py` under an Apache-2.0-only statement without a separate
GPL-compliance review. The upstream license is authoritative:

- https://github.com/libnano/primer3-py
- https://github.com/primer3-org/primer3

## Optional and development dependencies

`dna-features-viewer` is optional. Test and development tools such as pytest,
pytest-asyncio, pytest-mock, pytest-cov (MIT), mutmut (BSD-3-Clause), responses,
mypy, Ruff, and HTTPX are not runtime components of the project artifact. Their
upstream licenses still apply if they are redistributed.

## External tools and biological data

Cello is invoked through an external command boundary and is not bundled.
Licensing must be attached to the exact Cello implementation and revision:
CIDARLAB Cello v1 reports BSD-2-Clause, while the Cello 2.0 protocol references
MIT-licensed source. UCFs, gate libraries, sequence records, and experimental
datasets require independent source-level rights records; the compiler''s
license does not license those data.

No external Cello output or UCF data is present in the tracked Case 01 snapshot.

## Release gate

Before publishing a wheel, executable, container, hosted dataset, or evidence
bundle:

1. Generate a version-pinned dependency SBOM.
2. Include upstream license and NOTICE files for bundled packages.
3. Keep GPL components outside the Apache-2.0 base distribution unless reviewed.
4. Record exact licenses and rights URIs for every bundled UCF, part library,
   sequence source, dataset, and external-tool artifact.
5. Re-run the Evidence Governance tests and inspect the E-BOM claim decisions.
