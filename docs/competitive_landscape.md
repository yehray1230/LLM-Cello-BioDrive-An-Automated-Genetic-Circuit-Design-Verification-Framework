# Positioning and Comparison Landscape

Literature review date: 2026-07-27
Current public-doc review: 2026-08-30

> **Archive boundary:** The project is `CLOSED_UNSUCCESSFUL_ARCHIVED`. This
> comparison is a historical positioning aid, not an active competitor roadmap
> or a superiority benchmark. The final project completed zero real provider-
> to-Cello full-path projects; see [Archived Project Closeout](PROJECT_CLOSEOUT.md).

## Purpose and comparison boundary

This document compares public methods and inspectable artifacts. It is not a
complete feature audit, a performance benchmark, or proof that an unreported
capability does not exist.

Cello is an upstream design-automation system used by this project. CELLM is
the closest genetic-circuit natural-language comparison. GCAD extends the
current CAD landscape to mammalian genetic programs. CAPAS and the
verification-first catalysis literature are direct comparisons for the
evidence-governance thesis even though they are not circuit-design systems.

## Public positioning comparison

| System/source | Publicly emphasized role | Relationship to this project | Safe interpretation |
| --- | --- | --- | --- |
| Cello 2.0 | Converts a Verilog logic specification through Boolean-network and biological gate-mapping stages to DNA sequence and predicted performance. | Upstream CAD and mapping foundation. | Cello is the stronger reference for circuit synthesis and characterized libraries; this prototype does not claim to outperform it. |
| CELLM | Uses LLMs, LangChain, and Cello for natural-language creation, analysis, and optimization of genetic circuits. | Closest natural-language circuit comparator. | Natural-language access and LLM orchestration are not differentiation. |
| GCAD | Searches mammalian genetic-program designs from specifications, parts/interactions, ODE models, and a genetic algorithm. | Current CAD comparator beyond Cello. | Genetic-circuit CAD is a broader and active field; this project is not a comprehensive CAD benchmark. |
| CRISPR-GPT / GeneAgent / Virtual Lab | Agentic biological design, tool grounding, self-verification, or multi-agent scientific work. | Adjacent biological-agent systems. | Multi-agent design and self-verification are not unique and some adjacent systems have stronger external validation. |
| PROV-O / RO-Crate / nanopublications / ECO | Machine-readable provenance, research-object packaging, assertion-level publication, and evidence typing. | Standards and conceptual foundations. | Generic provenance, claim-level evidence objects, and machine-readable research packaging are established. |
| Verification-first autonomous catalysis | Proposes typed claims, admissible evidence, verifier, evidence ledger, uncertainty gate, and human escalation. | Direct conceptual overlap. | The broad verifier–ledger–gate architecture cannot be claimed as new here. |
| CAPAS | Public deterministic, fail-closed scientific claim-admissibility gate with structured evidence, provenance blockers, licensing/reproducibility checks, and replayable hashes. | Direct implementation overlap. | The general deterministic claim gate cannot remain a stand-alone contribution. The reviewed claims are from its official page and were not independently reproduced here. |
| This prototype | Applies a project-specific E-BOM schema and reportability rules to a genetic-circuit candidate workflow and a fixed public case. | Potential domain-specific implementation/case study. | The defensible route is a standards-aware genetic-circuit profile and fixed-case failure/reconstruction study, not general evidence-governance novelty. |

## Differentiation audit

| Proposed dimension | P1 finding | Permitted wording |
| --- | --- | --- |
| Natural-language entry | Established by CELLM and adjacent biological agents. | Implementation detail only. |
| Multi-agent orchestration | Established in biological and general scientific-agent systems. | Architecture detail only. |
| Machine-readable provenance | Established by PROV-O and RO-Crate. | State which standards or concepts are reused/mapped. |
| Claim-level evidence objects | Established by nanopublications, ECO, and newer claim-evidence systems. | Describe the repository-specific profile and fields. |
| Deterministic fail-closed claim gate | Directly overlaps CAPAS; conceptually overlaps verification-first catalysis. | Describe local conformance behavior, not novelty. |
| Rights-aware eligibility | CAPAS publicly includes licensing blockers; generic rights metadata is also established. | Describe exact local rules and limitations; never claim legal compliance. |
| Genetic-circuit evidence hierarchy | No exact reviewed intersection was found, but bounded absence is not uniqueness evidence. | A domain-specific composition tested on fixed Case 01. |
| Biological performance | Current repository has no wet-lab or accepted external-comparator evidence. One historical Cello 2.1 result is mapping-only. | Explicit limitation only. |

## Current bounded project description

> A research-preview implementation of a genetic-circuit-specific evidence
> profile, evaluated on a fixed public case for reconstructable reportability
> decisions, explicit blockers, and preserved computational, external-tool,
> sequence, real-data, wet-lab, and rights boundaries.

Short form:

> An inspectable evidence-profile case study for genetic-circuit candidate
> workflows.

The term E-BOM may remain the local profile name, but it must not imply that a
universal evidence bill of materials or deterministic scientific claim gate was
invented here.

## Claims to avoid

- first or unique machine-readable evidence contract;
- first or unique deterministic/fail-closed scientific claim gate;
- more trustworthy or more inspectable than another system without a
  predeclared comparative study;
- biologically valid, ready-to-build, externally mapped, or wet-lab validated;
- solves hallucination;
- license compliant as an unconditional guarantee;
- treating multi-agent orchestration, provenance, abstention, self-verification,
  or research-object packaging as stand-alone novelty;
- treating the bounded P1 negative search as proof that no exact competitor
  exists.

## Project evidence

- [Public scope](PUBLIC_SCOPE.md)
- [Evidence Governance and E-BOM Specification](evidence_governance_spec.md)
- [Case 01 public evidence](evidence/case_01/README.md)
- [Case 01 machine-readable E-BOM](evidence/case_01/evidence_manifest.json)
- [Project limitations](limitations.md)
- [Third-party notices and license boundaries](../THIRD_PARTY_NOTICES.md)

## Key external sources

- Nielsen et al., [Genetic circuit design automation](https://doi.org/10.1126/science.aac7341), *Science* (2016).
- Jones et al., [Genetic circuit design automation with Cello 2.0](https://doi.org/10.1038/s41596-021-00675-2), *Nature Protocols* (2022).
- Abello Castillo and Gutiérrez Pescarmona, [CELLM](https://doi.org/10.1021/acssynbio.5c00391), *ACS Synthetic Biology* (2025).
- Dreyer et al., [GCAD](https://doi.org/10.1021/acssynbio.5c00670), *ACS Synthetic Biology* (2026).
- Qu et al., [CRISPR-GPT](https://doi.org/10.1038/s41551-025-01463-z), *Nature Biomedical Engineering* (published online 2025; volume 2026).
- W3C, [PROV-O](https://www.w3.org/TR/prov-o/).
- Soiland-Reyes et al., [Packaging research artefacts with RO-Crate](https://doi.org/10.3233/DS-210053), *Data Science* (2022).
- Liu and Ou, [Verification-first autonomous catalysis](https://doi.org/10.1038/s44387-026-00111-4), *npj Artificial Intelligence* (2026).
- [CAPAS deterministic scientific claim gate](https://capas.krenniq.com/) (official implementation page; not peer-reviewed in this review).

Before using this comparison for a publication or release decision, refresh the
full bibliography and recheck the linked primary sources.
