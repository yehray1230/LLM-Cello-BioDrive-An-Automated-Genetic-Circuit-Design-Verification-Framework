# Project Limitations

This document states what the current project can and cannot claim. It is meant
to reduce ambiguity when presenting the work to synthetic biologists,
supervisors, collaborators, or potential research contacts.

Short version:

> This archived project implemented a research-preview workflow for generating
> and evaluating computational regulatory-logic candidates. It completed zero
> real provider-to-Cello full-path projects and did not produce a complete,
> buildable, experimentally validated genetic circuit.

## Current Stage

The project is a permanently archived `0.x research preview`. It reached
`CLOSED_UNSUCCESSFUL_ARCHIVED` on 2026-08-30 after an independent final
closeout. This status records a bounded stopping decision, not scientific or
production acceptance.

## Recorded Closeout Result

- Real provider-to-Cello full-path projects completed: **0**.
- Provider qualification attempts in the final closeout: **0**.
- Provider calls and observed/billed provider cost: **0 calls; USD 0**.
- Historical external-tool evidence: **one separate AND2 Cello 2.1
  mapping-only case**, not an integrated project or repeatability result.
- Terminal integration finding: the canonical service boundary did not enforce
  the frozen Gemini provider, transport, and reported-model identity after the
  two permitted implementation revisions were consumed.

See [Archived Project Closeout](PROJECT_CLOSEOUT.md) for the sanitized evidence
summary and stopping rationale.

It is appropriate to present it as:

- a computational design-assistance workflow;
- a natural-language-to-regulatory-logic prototype;
- a multi-agent workflow for proposal, translation, evaluation, critique, and
  repair;
- a way to produce traceable candidate design artifacts;
- a research prototype for discussing how AI-assisted design tools should
  expose biological uncertainty.

It is not appropriate to present it as:

- a finished synthetic-biology CAD tool;
- a validated biological design platform;
- a complete plasmid-design system;
- an automated experimental protocol generator;
- evidence that generated circuits will work in vivo.

## What This Project Can Do

The current prototype can:

- convert a natural-language design intent into computational circuit-design
  candidates;
- generate Boolean-logic proposals and Cello-compatible combinational Verilog;
- run an iterative multi-agent workflow for proposal, translation, evaluation,
  critique, and repair;
- use heuristic benchmark scores to compare candidates under the same
  implemented assumptions;
- run simplified ODE, stochastic, temporal, and perturbation-oriented screening
  paths where the required inputs are available;
- surface failure modes such as logic mismatch, weak simulated robustness,
  excessive complexity, missing evidence, or likely Cello/part-assignment
  problems;
- contain an optional external Cello execution boundary when a real command and
  compatible UCF/library data are configured; the final canonical integrated
  path was not provider-qualified or demonstrated end to end;
- label mock, failed, and externally mapped Cello outputs separately;
- evaluate sequence-level constraints when sequence data is available;
- produce conservative planning artifacts such as BOM, GenBank, SBOL3, and
  assembly-related reports when enough evidence is present.

These capabilities are useful for research prototyping, workflow design, and
early candidate triage.

## What This Project Cannot Yet Do

The current prototype cannot:

- design a complete plasmid from end to end;
- specify every sequence-level detail needed for construction;
- guarantee biological buildability;
- guarantee that a generated design is an experimentally validated genetic
  logic gate;
- replace expert synthetic-biology review;
- replace real Cello configuration, UCF selection, or part-library validation;
- predict in vivo expression quantitatively;
- prove host compatibility, biosafety, or regulatory compliance;
- reliably select experimentally characterized parts unless appropriate data
  are provided;
- generate wet-lab-ready primers, oligo orders, PCR conditions, or protocols;
- replace rigorous thermodynamic RNA-folding packages, calibrated host-cell
  models, or empirical characterization.

These limitations define the archived result. The system should be treated as
a computational design-assistance exploration, not as an automated
biological-design platform or an active product roadmap.

## Safe Claims

These are appropriate ways to describe the project:

> The system generates and evaluates computational candidate designs for
> regulatory logic circuits.

> The workflow translates natural-language intent into Boolean logic,
> Cello-compatible Verilog, simulated dynamics, and heuristic benchmark scores.

> The benchmark ranks candidates under implemented computational checks and
> exposes failure modes for iterative repair.

> The ODE simulator provides simplified screening evidence, not calibrated
> in vivo prediction.

> External Cello mapping is only available when a real Cello command and
> compatible UCF/library data are configured.

> Mock Cello output may be used for workflow testing, but should be labeled as
> mock-only and not described as real part assignment.

> Sequence, host, and assembly reports are planning artifacts. They are not
> construction instructions or experimental validation.

## Claims To Avoid

These statements would overstate the current system:

> The system automatically designs complete plasmids.

> The generated circuit is guaranteed to be buildable.

> A high benchmark score proves that the circuit will function experimentally.

> Mock Cello output is equivalent to real Cello mapping.

> The ODE simulation predicts real cellular expression quantitatively.

> ODE readouts such as peak output or time to peak are calibrated experimental
> measurements.

> The project has validated a biological logic gate without construction and
> measurement.

> Synonymous codon optimization guarantees high expression, structural
> stability, or biological function.

> Experimental calibration data automatically fits a validated dynamic host
> model.

## Evidence Needed For Stronger Claims

Stronger biological claims would require additional evidence and implementation
work, such as:

- real Cello execution with appropriate UCF files and mapped biological parts;
- sequence-level design of promoters, RBSs, coding regions, terminators,
  backbone, origin, marker, and cloning strategy;
- host-specific parameter calibration from literature or experiment;
- explicit modeling of plasmid copy number, growth dilution, burden-growth
  coupling, and toxicity;
- experimental measurement of ON/OFF ratios, response time, burden, growth
  effects, stability, and noise;
- comparison against known measured genetic circuits;
- validation of benchmark weights against empirical outcomes;
- expert review for biosafety, host compatibility, and experimental
  feasibility.

## Recommended One-Sentence Description

For presentations, emails, or early research conversations, use:

> This is an LLM-orchestrated computational design-assistance workflow built
> around Cello that translates natural-language regulatory-logic intent into
> candidate genetic-circuit representations, then ranks and critiques those
> candidates using simplified simulation and heuristic evaluation.
