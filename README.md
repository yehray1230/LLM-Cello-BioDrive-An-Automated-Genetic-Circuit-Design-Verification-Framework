# Evidence-Aware Genetic Circuit Design Research Prototype

AI systems can generate fluent genetic-circuit descriptions, but fluency is not
evidence of logical consistency, biological plausibility, buildability, or
experimental function.

This repository explores a working hypothesis:

> AI-assisted biological design should be treated as an evidence-aware
> evaluation process, not only as a generation task.

The current prototype translates regulatory-logic intent into computational
candidates, subjects them to deterministic checks and simplified models,
records missing evidence, and routes weak candidates toward critique or
revision. It is an exploratory, AI-assisted implementation of that idea—not a
virtual cell, a complete biological CAD platform, or an experimentally
validated circuit-design system.

> **Archived research preview:** This repository is a permanently archived
> `0.x research preview`, not an actively developed biological CAD product. Its
> public evidence covers inspectable software behavior, deterministic contracts,
> the Case 01 evidence-governance snapshot, and a bounded historical closeout.
> The project completed **zero real provider-to-Cello full-path projects** and
> provides no biological, wet-lab, buildability, production, or publication
> validation.

## What Makes This Prototype Different

Natural-language input, multi-agent orchestration, and Cello-compatible output
are parts of the workflow, but they are not the primary differentiation. The
research focus is an **evidence-governance layer** for deciding what may be
claimed about an AI-generated candidate.

- A machine-readable Evidence Bill of Materials (E-BOM) links public claims to
  named evidence, provenance, biological context, and intended use.
- Deterministic claim and license gates classify claims as `supported`,
  `limited`, `unsupported`, or `blocked`.
- Missing experimental evidence remains explicitly `unsupported`; it is never
  treated as evidence that an experiment failed.
- Project-owned Apache-2.0 material is kept distinct from third-party software,
  UCFs, part libraries, sequence records, and experimental data whose upstream
  terms still apply.

The intended output is therefore not only a candidate design. It is a candidate
plus an inspectable account of which statements are supported, by what, under
which rights and biological constraints, and what evidence is still missing.
See the [Evidence Governance and E-BOM Specification](docs/evidence_governance_spec.md)
and the [positioning and comparison landscape](docs/competitive_landscape.md).

## The Problem

Natural-language and LLM-generated biological designs can be persuasive while
silently crossing several abstraction boundaries:

- a coherent sentence is not a valid logic specification;
- valid Verilog is not a biological part assignment;
- a mapped or exported representation is not a buildable construct;
- a completed simulation is not calibrated in vivo prediction;
- a high heuristic score is not experimental evidence.

The project asks how a design workflow can preserve user intent while making
these boundaries, assumptions, failure modes, and next evidence requirements
visible.

## Working Hypothesis

A useful AI-assisted scientific workflow should combine generative components
with deterministic checks, explicit evidence classes, provenance, and honest
failure states. Its output should be a reviewable candidate and an account of
what supports it—not a confident-looking final answer.

This prototype therefore separates:

- LLM-dependent interpretation, proposal, translation, and critique;
- deterministic logic, schema, sequence, simulation, and export checks;
- mock, failed, and external Cello outcomes;
- computational scores from assembly and experimental readiness;
- available evidence from missing or inapplicable evidence.

## What the Prototype Tests

Given a request such as:

```text
Activate GFP only when input A is present and input B is absent.
```

the workflow can produce and inspect:

- a structured interpretation and truth-table expectation;
- Boolean logic and Cello-compatible combinational Verilog;
- explicit mock-versus-external Cello provenance;
- simplified resource-aware ODE, stochastic, temporal, and perturbation paths;
- versioned heuristic scoring and staged readiness reports;
- Critic feedback and repair provenance;
- sequence, host, assembly, BOM, GenBank, and SBOL3 planning artifacts when
  required evidence is available.

The implementation is a test bed for asking whether those layers can make
uncertainty and unsupported assumptions easier to inspect.

## Current Evidence

The repository includes deterministic regression coverage, versioned scoring
profiles, content-addressed benchmark metadata, explicit claim-boundary
policies, and a sanitized public snapshot for the fixed Case 01 demonstration.

The final bounded closeout also preserves two narrower findings:

- one separate historical AND2 case reached a computational external Cello 2.1
  mapping pass with one attempt, no retry, zero provider calls, and USD 0
  provider cost; it is mapping-only evidence, not an integrated project;
- an independent integration review found that the canonical service path did
  not enforce the accepted Gemini provider, transport, and reported-model
  identity. The two-revision budget was exhausted, so provider qualification
  and the planned real-project portfolio were not attempted.

See the [public project closeout](docs/PROJECT_CLOSEOUT.md) for the outcome,
evidence classes, stopping decision, and reusable lessons.

- [Case 01 public evidence](docs/evidence/case_01/README.md)
- [Case 01 machine-readable E-BOM](docs/evidence/case_01/evidence_manifest.json)
- [Demo summary](demo_cases/DEMO_SUMMARY.md)
- [Evaluation metrics](docs/evaluation_metrics.md)
- [Model assumptions](docs/model_assumptions.md)
- [Public result scope](docs/PUBLIC_SCOPE.md)
- [Archived project closeout](docs/PROJECT_CLOSEOUT.md)

## Public Result Scope

The GitHub-facing result surface is intentionally limited to evidence that can
be inspected and reproduced from the public tree:

- Case 01 public evidence and deterministic E-BOM governance checks;
- software contracts, deterministic checks, workflow, and provenance tests;
- computational preview behavior under named assumptions.

The public closeout summarizes the historical mapping-only record and terminal
integration No-Go without publishing the underlying local control packages.
EXP-024/Cello raw artifacts, preprint material, generated outputs, external
toolchain runtime files, and local run directories remain outside the default
GitHub surface. See the [public scope](docs/PUBLIC_SCOPE.md) for the exact
boundary.

### One-Minute Evidence Governance Proof

From the repository root, run:

```powershell
.\venv\Scripts\python.exe -m src.scripts.verify_evidence_manifest
```

The command independently rebuilds the recorded claim, license, and summary
decisions from the public Case 01 E-BOM. A `PASS` confirms that those governance
decisions reproduce and prints every supported, limited, unsupported, and
blocked claim. It does not mean that every biological claim is supported.
Use `--json` for a machine-readable proof result. See the
[Case 01 one-minute proof](docs/evidence/case_01/README.md#one-minute-public-proof-gate)
for the expected decisions and their interpretation.

The bundled `research_smoke_v1` benchmark contains synthetic infrastructure
fixtures, not measured circuits. Current evidence supports software and
computational-screening claims only.

## Evidence and Biological Boundaries

The project can support:

- computational representation of regulatory-logic intent;
- logic and syntax consistency checks;
- comparisons under named computational assumptions;
- identification of missing evidence and likely failure modes;
- conservative sequence, host, assembly, and exchange-format review paths.

It cannot currently claim:

- experimentally validated genetic logic gates;
- guaranteed biological buildability or complete plasmid design;
- calibrated in vivo expression prediction;
- reliable host compatibility, biosafety, or regulatory compliance;
- automatic selection of characterized parts without supplied evidence;
- wet-lab-ready primers, protocols, or expert approval.

The detailed claim policy is in [Project Limitations](docs/limitations.md).

## Implemented Preview Surface

| Area | Reviewable computational path | Evidence boundary |
| --- | --- | --- |
| Design intake | Natural-language elicitation, structured specifications, imports, revisions, comparisons, replacement checks | Generated or imported records require review |
| Circuit execution | Synchronous and persistent runs, events, feedback/resume, artifacts, monitoring | Run completion does not imply biological validity |
| Simulation | Resource-aware ODE, temporal inputs, sweeps, simplified bifurcation reports, perturbation, bounded SSA, retroactivity, operon and RBS heuristics | Models are simplified and mostly uncalibrated |
| Evaluation and repair | Versioned profiles, datasets, readiness domains, Critic routing, constrained repair provenance | Scores rank implemented checks; they are not probabilities |
| Sequence and host | Sequence QC, synonymous revisions, CAI/rare-codon reporting, host profiles and ranking | Expression and compatibility are not guaranteed |
| Assembly and exchange | Backbone registry, planning, deliverable packages, BOM, GenBank, SBOL3 | Planning and exchange artifacts are not protocols |
| Interfaces | FastAPI/OpenAPI, HTML workspace, MCP tools, maintenance-only Streamlit UI | Optional external services may be unavailable |

## Research Direction: Multi-Layer Resource Accounting

One future direction is a layered mathematical representation across the
central-dogma information flow:

```text
DNA copy-number and promoter context
  -> transcriptional demand and RNAP allocation
  -> RNA production, accessibility, and degradation
  -> translational demand and ribosome allocation
  -> protein maturation and degradation
  -> circuit output, burden, and growth-dilution feedback
```

The goal is not to reproduce a real cell or construct a whole-cell digital
twin. The model would provide a diagnostic lens for identifying assumed
resource bottlenecks, comparing candidates under explicit assumptions,
exposing missing parameters, and recommending the next evidence needed for a
stronger interpretation.

See the [future roadmap](docs/future_roadmap.md) for this and other open research
questions.

## Workflow Snapshot

```text
Natural-language intent
  -> structured specification
  -> candidate logic and Verilog
  -> external-or-mock Cello boundary
  -> data and parameter attachment
  -> simplified simulation and evaluation
  -> critique, repair, or consolidation
  -> reviewable artifacts plus explicit limitations
```

The main orchestration path is
[`src/workflows/reflexion_controller.py`](src/workflows/reflexion_controller.py).

## Read This Repository by Interest

Different readers need different evidence first. The audience guides change
emphasis and reading order, but never change the underlying claim boundaries.

| Interest | Start here |
| --- | --- |
| Synthetic biology or wet lab | [Synthetic biology guide](docs/audiences/synthetic_biology.md) |
| Mathematical or systems modeling | [Mathematical modeling guide](docs/audiences/mathematical_modeling.md) |
| AI4Science or agent systems | [AI4Science and agents guide](docs/audiences/ai4science_agents.md) |
| Bio-CAD, APIs, or interoperability | [Bio-CAD and interoperability guide](docs/audiences/bio_cad_interoperability.md) |
| Potential collaborators or reviewers | [Collaboration and review guide](docs/audiences/potential_collaborators.md) |

AI assistants should begin with [`llms.txt`](llms.txt), which provides the same
audience-aware routing while preserving a universal project identity.

## Repository Guide

| File | Purpose |
| --- | --- |
| [Quickstart](QUICKSTART.md) | Install, test, and run the local interfaces |
| [Public result scope](docs/PUBLIC_SCOPE.md) | What is and is not part of the GitHub-facing result surface |
| [Archived project closeout](docs/PROJECT_CLOSEOUT.md) | Final outcome, evidence boundary, stopping decision, and lessons |
| [Limitations](docs/limitations.md) | Safe claims, non-goals, evidence requirements |
| [Architecture](docs/architecture.md) | Components and responsibilities |
| [Workflow](docs/workflow.md) | Execution, repair, mock, and fallback behavior |
| [Model assumptions](docs/model_assumptions.md) | Equations, parameters, and missing mechanisms |
| [Evaluation metrics](docs/evaluation_metrics.md) | Scores, versions, and interpretation |
| [Evidence governance specification](docs/evidence_governance_spec.md) | E-BOM, claim decisions, license gates, and interoperability boundaries |
| [Positioning and comparison landscape](docs/competitive_landscape.md) | Source-backed comparison with Cello and CELLM |
| [AI reviewer guide](docs/ai_reviewer_guide.md) | Repository review protocol |
| [Evidence capture guide (Traditional Chinese)](docs/evidence_capture_guide_zh-Hant.md) | Future-run capture procedure; not evidence that an external run has occurred |
| [Future roadmap](docs/future_roadmap.md) | Open research and engineering directions |

## Development Provenance

This repository should not be interpreted as a claim of solo implementation or
independent scientific validation. It is a concept-driven, AI-assisted research
prototype developed through iterative specification, generated and revised
code, deterministic tests, document review, and explicit claim-boundary work.

The appropriate basis for evaluating it is the clarity of the problem, the
inspectability of the implementation, the available evidence, and the honesty
of its unresolved questions—not an authorship narrative.

## License and Third-Party Software

Original project code, documentation, synthetic fixtures, and generated
evidence are licensed under [Apache-2.0](LICENSE).

Dependencies and external biological data retain their upstream terms; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). In particular,
`primer3-py` is GPL-2.0 and is intentionally excluded from the Apache-2.0
base installation. Install the optional `primer-design` extra only after
reviewing the GPL redistribution boundary. Cello, UCFs, part libraries, and
external datasets are not licensed by this repository's Apache-2.0 grant.
