from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from application.research import ResearchService
from application.settings import SettingsService
from application.design_draft_service import DesignDraftService
from application.notification_service import NotificationService
from benchmark_suite.benchmark_controller import evaluate_candidate
from benchmark_suite.dataset import (
    list_benchmark_datasets,
    load_benchmark_dataset,
)
from benchmark_suite.runner import (
    compare_benchmark_runs,
    run_benchmark_dataset,
)
from benchmark_suite.scoring_profiles import list_scoring_profiles
from exporters.bom_exporter import export_bom_csv
from exporters.export_result import ExportResult
from exporters.genbank_exporter import export_genbank
from exporters.assembly_deliverables import write_assembly_deliverables
from exporters.plasmid_tools import PlasmidAssemblyResult, assemble_plasmid_v2
from exporters.sbol3_exporter import export_sbol3_turtle
from importers.genbank_importer import genbank_to_import_draft
from mcp_server.run_store import RunStore
from mcp_server.service import (
    cancel_design_run,
    get_design_run_artifacts,
    get_design_run_events,
    get_design_run_result,
    get_design_run_status,
    list_design_runs,
    resume_design_run,
    start_design_run,
    submit_design_feedback,
)
from repositories.json_repository import JsonRepository
from repositories.factory import create_design_repository, repository_backend
from repositories.protocols import RecordRepository, RevisionRepository
from repositories.sqlite_repository import canonical_payload_hash
from schemas.backbone_registry import (
    BackboneRegistryEntry,
    backbone_entry_from_dict,
    create_backbone_entry,
    registry_key,
)
from benchmark_suite.readiness_evaluator import evaluate_readiness
from schemas.design_diff import compare_designs
from schemas.design_migrations import (
    design_ir_v2_to_v1_payload,
    migrate_design_payload_to_v2,
)
from schemas.design_ir import DesignIR, design_ir_from_dict
from schemas.design_ir_v2 import (
    DesignIRV2,
    DesignRevisionV2,
    ProvenanceRecordV2,
    design_ir_v2_from_dict,
)
from schemas.host_profile import (
    HostProfile,
    default_ecoli_profile,
    default_yeast_profile,
    default_mammalian_profile,
    host_profile_from_dict,
    apply_host_profile_to_topology,
)
from schemas.host_optimization import (
    calibration_from_dict,
    measurement_from_dict,
)
from schemas.import_draft import (
    ImportDraft,
    import_draft_from_json,
    import_draft_to_design_ir,
    validate_import_draft,
)
from schemas.simulation import (
    SIMULATION_MODEL_ID,
    SIMULATION_MODEL_VERSION,
    simulation_spec_from_design_ir_v2,
)
from schemas.sequence_optimization import SequenceOptimizationRequest
from tools.assembly_planner import create_assembly_plan
from tools.primer_designer import design_assembly_primers
from tools.ode_simulator import BatchODESimulator
from benchmark_suite.parameter_fitting import apply_parameter_fit_snapshot
from tools.tool_adapters import inspect_capabilities
from tools.sequence_analyzer import analyze_design_sequences
from tools.sequence_optimization import evaluate_sequence_optimization
from tools.sequence_optimization import generate_host_optimized_sequences
from tools.host_optimization import (
    rank_host_optimization_candidates,
    summarize_host_calibration,
)
from utils.scalar_values import optional_trimmed_text as _optional_string


DEFAULT_API_DATA_DIR = Path("outputs") / "api_data"
SAFE_RUN_ID = re.compile(r"^run_[A-Za-z0-9_-]{1,120}$")
SNAPSHOT_COMPARISON_REPORT_TYPE = "parameter_fit_snapshot_comparison"
SNAPSHOT_COMPARISON_REPORT_VERSION = "1.0"
SNAPSHOT_COMPARISON_METRICS = {
    "dynamic_margin": "Dynamic margin",
    "signal_to_noise_ratio": "Signal-to-noise ratio",
    "kinetic_score": "Kinetic score",
}


class ImportService:
    def __init__(
        self,
        draft_repository: JsonRepository,
        design_service: DesignService,
    ):
        self.drafts = draft_repository
        self.designs = design_service

    def validate(self, draft: ImportDraft) -> dict[str, Any]:
        validation = validate_import_draft(draft)
        return {**asdict(validation), "can_import": validation.can_import}

    def save_draft(self, draft: ImportDraft) -> ImportDraft:
        self.drafts.save(draft.draft_id, draft.to_dict())
        return draft

    def get_draft(self, draft_id: str) -> ImportDraft | None:
        payload = self.drafts.get(draft_id)
        return import_draft_from_json(payload) if payload else None

    def import_json(self, value: str | bytes | dict[str, Any]) -> ImportDraft:
        return self.save_draft(import_draft_from_json(value))

    def import_genbank(
        self,
        value: str | bytes,
        *,
        filename: str,
    ) -> ImportDraft:
        return self.save_draft(
            genbank_to_import_draft(value, filename=filename)
        )

    def confirm(self, draft: ImportDraft) -> DesignIR:
        self.save_draft(draft)
        design = import_draft_to_design_ir(draft)
        return self.designs.save(design)

    def confirm_by_id(self, draft_id: str) -> DesignIR:
        draft = self.get_draft(draft_id)
        if draft is None:
            raise KeyError(draft_id)
        return self.confirm(draft)


class DesignService:
    def __init__(self, repository: RecordRepository):
        self.repository = repository

    def save(self, design: DesignIR) -> DesignIR:
        source_payload = design.to_dict()
        migration = migrate_design_payload_to_v2(source_payload)
        stored = self.repository.save(design.design_id, migration.design.to_dict())
        self._record_migration(
            design.design_id,
            source_payload=source_payload,
            result_payload=stored,
            migration=migration.to_dict(),
        )
        return design_ir_from_dict(design_ir_v2_to_v1_payload(stored))

    def save_v2(self, design: DesignIRV2) -> DesignIRV2:
        stored = self.repository.save(design.design_id, design.to_dict())
        return design_ir_v2_from_dict(stored)

    def get(self, design_id: str) -> DesignIR | None:
        payload = self.repository.get(design_id)
        return (
            design_ir_from_dict(design_ir_v2_to_v1_payload(payload))
            if payload
            else None
        )

    def list(self, show_archived: bool = False, show_deleted: bool = False) -> list[DesignIR]:
        import inspect
        sig = inspect.signature(self.repository.list)
        if "show_archived" in sig.parameters:
            payloads = self.repository.list(show_archived=show_archived, show_deleted=show_deleted)
        else:
            payloads = self.repository.list()
            payloads = [
                p for p in payloads
                if (show_archived or not p.get("is_archived"))
                and (show_deleted or not p.get("is_deleted"))
            ]
        payloads.sort(key=lambda x: x.get("is_pinned", False), reverse=True)
        return [
            design_ir_from_dict(design_ir_v2_to_v1_payload(payload))
            for payload in payloads
        ]

    def get_v2(self, design_id: str) -> DesignIRV2 | None:
        payload = self.repository.get(design_id)
        return design_ir_v2_from_dict(payload) if payload else None

    def list_v2(self, show_archived: bool = False, show_deleted: bool = False) -> list[DesignIRV2]:
        import inspect
        sig = inspect.signature(self.repository.list)
        if "show_archived" in sig.parameters:
            payloads = self.repository.list(show_archived=show_archived, show_deleted=show_deleted)
        else:
            payloads = self.repository.list()
            payloads = [
                p for p in payloads
                if (show_archived or not p.get("is_archived"))
                and (show_deleted or not p.get("is_deleted"))
            ]
        payloads.sort(key=lambda x: x.get("is_pinned", False), reverse=True)
        return [
            design_ir_v2_from_dict(payload)
            for payload in payloads
        ]

    def revisions(self, design_id: str) -> list[dict[str, Any]]:
        if not isinstance(self.repository, RevisionRepository):
            return []
        return self.repository.list_revisions(design_id)

    def get_revision(
        self,
        design_id: str,
        revision_number: int,
    ) -> DesignIRV2 | None:
        if not isinstance(self.repository, RevisionRepository):
            return None
        payload = self.repository.get_revision(design_id, revision_number)
        return design_ir_v2_from_dict(payload) if payload else None

    def archive(self, design_id: str) -> None:
        if hasattr(self.repository, "archive"):
            self.repository.archive(design_id)

    def unarchive(self, design_id: str) -> None:
        if hasattr(self.repository, "unarchive"):
            self.repository.unarchive(design_id)

    def soft_delete(self, design_id: str) -> None:
        if hasattr(self.repository, "soft_delete"):
            self.repository.soft_delete(design_id)

    def restore(self, design_id: str) -> None:
        if hasattr(self.repository, "restore"):
            self.repository.restore(design_id)

    def pin(self, design_id: str) -> None:
        if hasattr(self.repository, "pin"):
            self.repository.pin(design_id)

    def unpin(self, design_id: str) -> None:
        if hasattr(self.repository, "unpin"):
            self.repository.unpin(design_id)

    def purge(self, design_id: str) -> bool:
        if hasattr(self.repository, "purge"):
            return self.repository.purge(design_id)
        return False

    def simulation_spec(self, design_id: str) -> dict[str, Any] | None:
        design = self.get_v2(design_id)
        if design is None:
            return None
        return simulation_spec_from_design_ir_v2(design).to_dict()

    def _record_migration(
        self,
        design_id: str,
        *,
        source_payload: dict[str, Any],
        result_payload: dict[str, Any],
        migration: dict[str, Any],
    ) -> None:
        recorder = getattr(self.repository, "record_payload_migration", None)
        if not callable(recorder):
            return
        recorder(
            source_id=design_id,
            source_version=str(source_payload.get("schema_version") or "1.0"),
            target_version=str(result_payload.get("schema_version") or "2.0"),
            source_hash=canonical_payload_hash(source_payload),
            result_hash=canonical_payload_hash(result_payload),
            status="completed",
            report=migration,
        )


class ComparisonService:
    def __init__(self, designs: DesignService):
        self.designs = designs

    def compare(
        self,
        left_design_id: str,
        right_design_id: str,
        *,
        left_metrics: dict[str, Any] | None = None,
        right_metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        left = self.designs.get(left_design_id)
        right = self.designs.get(right_design_id)
        if left is None:
            raise KeyError(left_design_id)
        if right is None:
            raise KeyError(right_design_id)
        return asdict(
            compare_designs(
                left,
                right,
                left_metrics=left_metrics,
                right_metrics=right_metrics,
            )
        )

    def compare_revisions(
        self,
        design_id: str,
        left_rev: int,
        right_rev: int,
    ) -> dict[str, Any]:
        left_v2 = self.designs.get_revision(design_id, left_rev)
        right_v2 = self.designs.get_revision(design_id, right_rev)
        if left_v2 is None or right_v2 is None:
            raise KeyError(f"Revision {left_rev} or {right_rev} not found.")

        from schemas.design_ir import design_ir_from_dict
        from schemas.design_migrations import design_ir_v2_to_v1_payload
        left = design_ir_from_dict(design_ir_v2_to_v1_payload(left_v2.to_dict()))
        right = design_ir_from_dict(design_ir_v2_to_v1_payload(right_v2.to_dict()))

        return asdict(compare_designs(left, right))


class EvaluationService:
    def __init__(
        self,
        benchmark_repository: JsonRepository,
        parameter_fit_repository: JsonRepository,
        report_dir: Path,
        resource_calibration_repository: JsonRepository | None = None,
    ):
        self.benchmark_repository = benchmark_repository
        self.parameter_fit_repository = parameter_fit_repository
        self.report_dir = report_dir
        self.resource_calibration_repository = resource_calibration_repository

    def create_parameter_fit_snapshot(
        self,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot_id = request.get("snapshot_id") or f"fit_{uuid4().hex[:12]}"
        part_id = request.get("part_id", "unknown_part")

        from benchmark_suite.parameter_fitting import load_plate_reader_csv, fit_hill_response
        points = load_plate_reader_csv(
            source=request["csv_content"],
            concentration_column=request.get("concentration_column", "concentration"),
            response_column=request.get("response_column", "response"),
        )
        fit = fit_hill_response(
            points=points,
            source=str(request.get("source") or "local_plate_reader_fit"),
            measurement_context=dict(request.get("measurement_context") or {}),
        )

        from benchmark_suite.parameter_fitting import fitted_parameters_to_part_override
        override = fitted_parameters_to_part_override(
            part_id=part_id,
            fit=fit,
            snapshot_id=snapshot_id,
        )
        payload = {
            "snapshot_id": snapshot_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "completed",
            "part_id": part_id,
            "source": str(request.get("source") or "local_plate_reader_fit"),
            "measurement_context": dict(request.get("measurement_context") or {}),
            "fit": fit.to_dict(),
            "override": override,
            "warnings": list(fit.warnings),
            "data_boundary": "local_private",
            "update_policy": override["update_policy"],
        }
        self.parameter_fit_repository.save(snapshot_id, payload)
        return payload

    def parameter_fit_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        return self.parameter_fit_repository.get(snapshot_id)

    def parameter_fit_snapshots(self) -> list[dict[str, Any]]:
        return self.parameter_fit_repository.list()

    def create_resource_calibration_workflow(
        self,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        if self.resource_calibration_repository is None:
            raise RuntimeError("Resource calibration storage is not configured.")
        workflow_id = request.get("workflow_id") or f"resource_{uuid4().hex[:12]}"
        from benchmark_suite.resource_workflow import run_resource_calibration_workflow

        payload = run_resource_calibration_workflow(request)
        payload["workflow_id"] = workflow_id
        payload["created_at"] = datetime.now(timezone.utc).isoformat()
        self.resource_calibration_repository.save(workflow_id, payload)
        return payload

    def resource_calibration_workflow(
        self,
        workflow_id: str,
    ) -> dict[str, Any] | None:
        if self.resource_calibration_repository is None:
            return None
        return self.resource_calibration_repository.get(workflow_id)

    def resource_calibration_workflows(self) -> list[dict[str, Any]]:
        if self.resource_calibration_repository is None:
            return []
        return self.resource_calibration_repository.list()

    def analyze_resource_calibration_workflow(
        self,
        workflow_id: str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.resource_calibration_repository is None:
            raise RuntimeError("Resource calibration storage is not configured.")
        workflow = self.resource_calibration_repository.get(workflow_id)
        if workflow is None:
            raise KeyError(workflow_id)
        from benchmark_suite.resource_model_analysis import run_resource_model_analysis

        analysis = run_resource_model_analysis(workflow, config)
        analysis["analyzed_at"] = datetime.now(timezone.utc).isoformat()
        workflow["model_analysis"] = analysis
        self.resource_calibration_repository.save(workflow_id, workflow)
        return analysis

    def evaluate(
        self,
        candidate: dict[str, Any],
        *,
        profile_id: str = "research-v1.8",
    ) -> dict[str, Any]:
        return evaluate_candidate(candidate, profile_id=profile_id)

    def profiles(self) -> list[dict[str, Any]]:
        return list_scoring_profiles()

    def datasets(self) -> list[dict[str, Any]]:
        return list_benchmark_datasets()

    def dataset(self, dataset_id: str) -> dict[str, Any]:
        return load_benchmark_dataset(dataset_id).to_dict()

    def run_benchmark(
        self,
        dataset_id: str,
        *,
        profile_id: str = "research-v1.8",
    ) -> dict[str, Any]:
        dataset = load_benchmark_dataset(dataset_id)
        result = run_benchmark_dataset(
            dataset,
            profile_id=profile_id,
            output_dir=self.report_dir,
        )
        self.benchmark_repository.save(result["benchmark_run_id"], result)
        return result

    def benchmark_result(self, benchmark_run_id: str) -> dict[str, Any] | None:
        return self.benchmark_repository.get(benchmark_run_id)

    def benchmark_results(self) -> list[dict[str, Any]]:
        return self.benchmark_repository.list()

    def compare_benchmarks(
        self,
        benchmark_run_ids: list[str],
    ) -> dict[str, Any]:
        runs = []
        for run_id in benchmark_run_ids:
            result = self.benchmark_result(run_id)
            if result is None:
                raise KeyError(run_id)
            runs.append(result)
        return compare_benchmark_runs(runs)


def _metric_delta(
    metric: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    default_value = before.get(metric)
    fitted_value = after.get(metric)
    delta = (
        round(float(fitted_value) - float(default_value), 6)
        if default_value is not None and fitted_value is not None
        else None
    )
    return {
        "metric": metric,
        "label": SNAPSHOT_COMPARISON_METRICS[metric],
        "default": default_value,
        "fitted": fitted_value,
        "delta": delta,
        "direction": _delta_direction(delta),
    }


def _delta_direction(delta: float | None) -> str:
    if delta is None:
        return "unknown"
    if delta > 0:
        return "improved"
    if delta < 0:
        return "reduced"
    return "unchanged"


def _provenance_count(summary: dict[str, Any], key: str) -> int:
    value = summary.get(key, 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _provenance_delta(
    default_provenance: dict[str, Any],
    fitted_provenance: dict[str, Any],
) -> dict[str, Any]:
    fields = {
        "override_count": "override_count",
        "local_private_parameter_count": "local_private_parameter_count",
    }
    deltas: dict[str, Any] = {}
    for report_key, provenance_key in fields.items():
        before = _provenance_count(default_provenance, provenance_key)
        after = _provenance_count(fitted_provenance, provenance_key)
        deltas[report_key] = {
            "default": before,
            "fitted": after,
            "delta": after - before,
        }
    return {
        "counts": deltas,
        "source_summary_default": default_provenance.get("source_summary", {}),
        "source_summary_fitted": fitted_provenance.get("source_summary", {}),
        "data_boundary_summary_default": default_provenance.get(
            "data_boundary_summary", {}
        ),
        "data_boundary_summary_fitted": fitted_provenance.get(
            "data_boundary_summary", {}
        ),
    }


def _snapshot_comparison_interpretation(
    metric_deltas: list[dict[str, Any]],
) -> dict[str, Any]:
    indexed = {item["metric"]: item for item in metric_deltas}
    kinetic = indexed.get("kinetic_score", {}).get("delta")
    snr = indexed.get("signal_to_noise_ratio", {}).get("delta")
    margin = indexed.get("dynamic_margin", {}).get("delta")
    return {
        "status": "complete",
        "primary_direction": _delta_direction(kinetic),
        "improved_metrics": [
            item["metric"] for item in metric_deltas if item["direction"] == "improved"
        ],
        "reduced_metrics": [
            item["metric"] for item in metric_deltas if item["direction"] == "reduced"
        ],
        "summary": (
            f"Fitted snapshot comparison completed: kinetic_score delta={kinetic}, "
            f"SNR delta={snr}, dynamic_margin delta={margin}."
        ),
    }


class SimulationService:
    def __init__(
        self,
        parameter_fit_repository: JsonRepository | None = None,
        host_profiles: HostProfileRegistryService | None = None,
    ):
        self.parameter_fit_repository = parameter_fit_repository
        self.host_profiles = host_profiles

    def models(self) -> list[dict[str, Any]]:
        return [
            {
                "model_id": SIMULATION_MODEL_ID,
                "version": SIMULATION_MODEL_VERSION,
                "status": "computational_screening",
                "state_model": "mRNA, immature protein, mature protein",
                "mechanisms": [
                    "regulatory topology",
                    "RNAP and ribosome competition",
                    "growth dilution",
                    "protein maturation",
                    "plasmid copy-number scaling",
                ],
            }
        ]

    def simulate(
        self,
        topology: dict[str, Any],
        *,
        simulation_time: float = 600.0,
        sample_count: int = 80,
        monte_carlo_samples: int = 1,
        noise_fraction: float = 0.15,
        random_seed: int | None = None,
        host_profile_id: str | None = None,
        temporal_inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Apply host profile if provided or if we can resolve one
        if host_profile_id:
            if self.host_profiles:
                profile = self.host_profiles.get(host_profile_id)
                if not profile:
                    raise ValueError(f"Host profile '{host_profile_id}' not found.")
                topology = apply_host_profile_to_topology(topology, profile)
        else:
            # Fallback/resolve from topology if no host_profile_id is explicitly passed
            chassis = topology.get("chassis") or topology.get("biokinetic_parameters", {}).get("host")
            if chassis and self.host_profiles:
                ch = str(chassis).lower().strip()
                resolved_id = None
                if "yeast" in ch or "cerevisiae" in ch:
                    resolved_id = "yeast_sc_default"
                elif "coli" in ch:
                    resolved_id = "ecoli_k12_default"
                elif "cho" in ch or "mammalian" in ch or "sapiens" in ch:
                    resolved_id = "mammalian_cho_default"

                if resolved_id:
                    profile = self.host_profiles.get(resolved_id)
                    if profile:
                        topology = apply_host_profile_to_topology(topology, profile)

        simulator = BatchODESimulator(
            simulation_time=simulation_time,
            sample_count=sample_count,
            monte_carlo_samples=monte_carlo_samples,
            noise_fraction=noise_fraction,
            random_seed=random_seed,
            temporal_inputs=temporal_inputs,
        )
        result = simulator.simulate_topology(topology)
        return {
            "simulation_spec": result["simulation_spec"],
            "simulation_result": result["simulation_result"],
            "candidate": result,
        }

    def apply_parameter_fit_snapshot(
        self,
        topology: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        return apply_parameter_fit_snapshot(topology, snapshot)

    def compare_default_vs_fitted(
        self,
        topology: dict[str, Any],
        snapshot_id: str,
        *,
        simulation_time: float = 600.0,
        sample_count: int = 80,
        monte_carlo_samples: int = 1,
        noise_fraction: float = 0.15,
        random_seed: int | None = None,
        host_profile_id: str | None = None,
        temporal_inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.parameter_fit_repository:
            raise ValueError("Parameter fit repository is not configured.")
        snapshot = self.parameter_fit_repository.get(snapshot_id)
        if snapshot is None:
            raise KeyError(snapshot_id)

        # 1. Simulate default topology
        default_res = self.simulate(
            topology,
            simulation_time=simulation_time,
            sample_count=sample_count,
            monte_carlo_samples=monte_carlo_samples,
            noise_fraction=noise_fraction,
            random_seed=random_seed,
            host_profile_id=host_profile_id,
            temporal_inputs=temporal_inputs,
        )
        default_cand = default_res["candidate"]

        # 2. Simulate fitted topology (with snapshot overrides applied)
        fitted_topology = self.apply_parameter_fit_snapshot(topology, snapshot)
        fitted_res = self.simulate(
            fitted_topology,
            simulation_time=simulation_time,
            sample_count=sample_count,
            monte_carlo_samples=monte_carlo_samples,
            noise_fraction=noise_fraction,
            random_seed=random_seed,
            host_profile_id=host_profile_id,
            temporal_inputs=temporal_inputs,
        )
        fitted_cand = fitted_res["candidate"]

        def_prov = default_cand.get("parameter_provenance", {})
        fit_prov = fitted_cand.get("parameter_provenance", {})
        metric_deltas = [
            _metric_delta(metric, default_cand, fitted_cand)
            for metric in SNAPSHOT_COMPARISON_METRICS
        ]
        metric_delta_map = {
            item["metric"]: item["delta"] for item in metric_deltas
        }
        provenance_delta = _provenance_delta(def_prov, fit_prov)
        simulation_config = {
            "simulation_time": simulation_time,
            "sample_count": sample_count,
            "monte_carlo_samples": monte_carlo_samples,
            "noise_fraction": noise_fraction,
            "random_seed": random_seed,
            "host_profile_id": host_profile_id,
            "temporal_inputs": temporal_inputs,
            "simulation_model_id": SIMULATION_MODEL_ID,
            "simulation_model_version": SIMULATION_MODEL_VERSION,
        }
        generated_at = datetime.now(timezone.utc).isoformat()
        report = {
            "report_type": SNAPSHOT_COMPARISON_REPORT_TYPE,
            "report_version": SNAPSHOT_COMPARISON_REPORT_VERSION,
            "topology_id": topology.get("topology_id") or "unknown",
            "snapshot_id": snapshot_id,
            "part_id": snapshot.get("part_id"),
            "snapshot": {
                "snapshot_id": snapshot.get("snapshot_id"),
                "part_id": snapshot.get("part_id"),
                "source": snapshot.get("source"),
                "data_boundary": snapshot.get("data_boundary"),
                "measurement_context": snapshot.get("measurement_context", {}),
                "fit_quality": snapshot.get("fit_quality", {}),
            },
            "generated_at": generated_at,
            "simulation_config": simulation_config,
            "default_run": {
                "dynamic_margin": default_cand.get("dynamic_margin"),
                "signal_to_noise_ratio": default_cand.get("signal_to_noise_ratio"),
                "kinetic_score": default_cand.get("kinetic_score"),
                "parameter_provenance": def_prov,
                "ode_trace": default_cand.get("ode_trace"),
            },
            "fitted_run": {
                "dynamic_margin": fitted_cand.get("dynamic_margin"),
                "signal_to_noise_ratio": fitted_cand.get("signal_to_noise_ratio"),
                "kinetic_score": fitted_cand.get("kinetic_score"),
                "parameter_provenance": fit_prov,
                "ode_trace": fitted_cand.get("ode_trace"),
            },
            "metric_deltas": metric_deltas,
            "provenance_delta": provenance_delta,
            "interpretation": _snapshot_comparison_interpretation(metric_deltas),
            "comparison": {
                "dynamic_margin_delta": metric_delta_map["dynamic_margin"],
                "signal_to_noise_ratio_delta": metric_delta_map[
                    "signal_to_noise_ratio"
                ],
                "kinetic_score_delta": metric_delta_map["kinetic_score"],
                "provenance_changes": {
                    "override_count_before": provenance_delta["counts"][
                        "override_count"
                    ]["default"],
                    "override_count_after": provenance_delta["counts"][
                        "override_count"
                    ]["fitted"],
                    "local_private_count_before": provenance_delta["counts"][
                        "local_private_parameter_count"
                    ]["default"],
                    "local_private_count_after": provenance_delta["counts"][
                        "local_private_parameter_count"
                    ]["fitted"],
                },
                "summary": (
                    f"Applying snapshot {snapshot_id} "
                    f"changed kinetic_score by {metric_delta_map['kinetic_score']:+.4f} "
                    f"({default_cand.get('kinetic_score'):.4f} -> {fitted_cand.get('kinetic_score'):.4f}), "
                    f"SNR by {metric_delta_map['signal_to_noise_ratio']:+.4f} "
                    f"({default_cand.get('signal_to_noise_ratio'):.4f} -> {fitted_cand.get('signal_to_noise_ratio'):.4f}), "
                    f"and dynamic_margin by {metric_delta_map['dynamic_margin']:+.4f} "
                    f"({default_cand.get('dynamic_margin'):.4f} -> {fitted_cand.get('dynamic_margin'):.4f})."
                    if all(value is not None for value in metric_delta_map.values())
                    else f"Snapshot comparison finished for snapshot {snapshot_id}."
                ),
            }
        }
        hash_payload = {
            key: value
            for key, value in report.items()
            if key not in {"generated_at", "report_id", "report_hash"}
        }
        report_hash = canonical_payload_hash(hash_payload)
        report["report_hash"] = report_hash
        report["report_id"] = f"snapshot_comparison_{report_hash[:12]}"
        return report


class ToolCapabilityService:
    def inspect(self) -> dict[str, Any]:
        return inspect_capabilities()


class ExportService:
    EXPORTERS = {
        "bom": export_bom_csv,
        "genbank": export_genbank,
        "sbol3": export_sbol3_turtle,
    }

    def __init__(self, designs: DesignService):
        self.designs = designs

    def export(
        self,
        design_id: str,
        export_format: str,
        revision_number: int | None = None,
        backbone_name: str | None = None,
    ) -> ExportResult:
        if revision_number is not None:
            design_v2 = self.designs.get_revision(design_id, revision_number)
            if design_v2 is None:
                raise KeyError(design_id)
            design = design_ir_from_dict(design_ir_v2_to_v1_payload(design_v2.to_dict()))
        else:
            design = self.designs.get(design_id)
            design_v2 = self.designs.get_v2(design_id)
            if design is None or design_v2 is None:
                raise KeyError(design_id)

        fmt = export_format.lower()
        from utils.safety_checker import check_design_export_safety, log_safety_event

        safety_result = check_design_export_safety(design_v2.to_dict(), fmt)
        if safety_result.status != "safe":
            log_safety_event(
                design_id,
                design_v2.specification.user_intent or design_v2.name,
                safety_result.status,
                safety_result.warnings,
                action=f"export:{fmt}",
                categories=safety_result.categories,
                metadata={"revision_number": design_v2.revision.revision_number},
            )
        if not safety_result.export_allowed:
            return ExportResult(
                ok=False,
                format=fmt.upper(),
                filename=f"{design.design_id}.{fmt}",
                media_type="application/octet-stream",
                content="",
                status="blocked_safety_review",
                warnings=safety_result.warnings,
                errors=[
                    safety_result.redirection_message
                    or "Explicit human safety review is required before export."
                ],
                safety_status=safety_result.status,
                safety_review_required=safety_result.requires_human_review,
            )

        def with_safety(result: ExportResult) -> ExportResult:
            result.warnings = list(
                dict.fromkeys([*result.warnings, *safety_result.warnings])
            )
            result.safety_status = safety_result.status
            result.safety_review_required = safety_result.requires_human_review
            return result

        if fmt == "json":
            import json
            return with_safety(ExportResult(
                ok=True,
                format="JSON",
                filename=f"{design.design_id}.json",
                media_type="application/json",
                content=json.dumps(design_v2.to_dict(), indent=2, ensure_ascii=False),
                status="success",
            ))
        elif fmt == "verilog":
            verilog_content = design_v2.extensions.get("verilog", "")
            if not verilog_content:
                return with_safety(ExportResult(
                    ok=False,
                    format="Verilog",
                    filename=f"{design.design_id}.v",
                    media_type="text/x-verilog",
                    content="",
                    status="blocked_no_verilog",
                    errors=["No Verilog topology exists for this design."],
                ))
            return with_safety(ExportResult(
                ok=True,
                format="Verilog",
                filename=f"{design.design_id}.v",
                media_type="text/x-verilog",
                content=verilog_content,
                status="success",
            ))
        elif fmt == "plasmid_genbank":
            from exporters.plasmid_assembler import export_plasmid_genbank
            return with_safety(
                export_plasmid_genbank(
                    design,
                    backbone_name or "pUC19 (High copy, AmpR)",
                )
            )

        exporter = self.EXPORTERS.get(fmt)
        if exporter is None:
            raise ValueError(f"Unsupported export format: {export_format}")
        return with_safety(exporter(design))


class BackboneRegistryService:
    def __init__(self, repository: JsonRepository):
        self.repository = repository

    def register(self, payload: dict[str, Any]) -> BackboneRegistryEntry:
        entry = create_backbone_entry(payload)
        existing = self.get(entry.backbone_id, entry.version)
        if existing:
            if existing.sequence_checksum != entry.sequence_checksum:
                raise ValueError(
                    "Backbone version already exists with a different checksum."
                )
            return existing
        self.repository.save(entry.registry_key, entry.to_dict())
        return entry

    def get(
        self,
        backbone_id: str,
        version: str,
    ) -> BackboneRegistryEntry | None:
        payload = self.repository.get(registry_key(backbone_id, version))
        return backbone_entry_from_dict(payload) if payload else None

    def list(self) -> list[BackboneRegistryEntry]:
        return [
            backbone_entry_from_dict(payload)
            for payload in self.repository.list()
        ]


class HostProfileRegistryService:
    def __init__(self, repository: JsonRepository):
        self.repository = repository
        self.ensure_defaults()

    def ensure_defaults(self) -> None:
        defaults = [
            default_ecoli_profile(),
            default_yeast_profile(),
            default_mammalian_profile(),
        ]
        for default in defaults:
            if not self.repository.exists(default.profile_id):
                self.repository.save(default.profile_id, default.to_dict())

    def register(self, payload: dict[str, Any]) -> HostProfile:
        profile = host_profile_from_dict(payload)
        _validate_host_profile(profile)
        stored = self.repository.save(profile.profile_id, profile.to_dict())
        return host_profile_from_dict(stored)

    def get(self, profile_id: str) -> HostProfile | None:
        payload = self.repository.get(profile_id)
        return host_profile_from_dict(payload) if payload else None

    def list(self) -> list[HostProfile]:
        return [
            host_profile_from_dict(payload)
            for payload in self.repository.list()
        ]


class PlasmidAssemblyService:
    def __init__(
        self,
        designs: DesignService,
        backbones: BackboneRegistryService,
    ):
        self.designs = designs
        self.backbones = backbones

    def assemble(
        self,
        design_id: str,
        *,
        plasmid_id: str,
        backbone_id: str,
        backbone_version: str,
        insertion_region_id: str,
        insertion_start: int,
        insertion_end: int,
        assembly_method: str = "direct_insertion",
        revision_number: int | None = None,
    ) -> PlasmidAssemblyResult:
        if revision_number is not None:
            design = self.designs.get_revision(design_id, revision_number)
        else:
            design = self.designs.get_v2(design_id)
        if design is None:
            raise KeyError(design_id)
        backbone = self.backbones.get(backbone_id, backbone_version)
        if backbone is None:
            raise ValueError(
                f"Unknown registered backbone: {backbone_id}@{backbone_version}"
            )
        return assemble_plasmid_v2(
            design,
            plasmid_id=plasmid_id,
            backbone_genbank=backbone.genbank,
            insertion_start=insertion_start,
            insertion_end=insertion_end,
            assembly_method=assembly_method,
            backbone_entry=backbone,
            insertion_region_id=insertion_region_id,
        )


class AssemblyPlanningService:
    def __init__(
        self,
        assemblies: PlasmidAssemblyService,
        backbones: BackboneRegistryService,
    ):
        self.assemblies = assemblies
        self.backbones = backbones

    def plan(
        self,
        design_id: str,
        *,
        plasmid_id: str,
        backbone_id: str,
        backbone_version: str,
        insertion_region_id: str,
        insertion_start: int,
        insertion_end: int,
        method: str,
        restriction_enzymes: list[str] | None = None,
        gibson_overlap_length: int = 25,
        golden_gate_enzyme: str = "BsaI",
        golden_gate_overhangs: list[str] | None = None,
        revision_number: int | None = None,
    ) -> dict[str, Any]:
        backbone = self.backbones.get(backbone_id, backbone_version)
        if backbone is None:
            raise ValueError(
                f"Unknown registered backbone: {backbone_id}@{backbone_version}"
            )
        if revision_number is not None:
            design = self.assemblies.designs.get_revision(design_id, revision_number)
        else:
            design = self.assemblies.designs.get_v2(design_id)
        if design is None:
            raise KeyError(design_id)
        assembly = self.assemblies.assemble(
            design_id,
            plasmid_id=plasmid_id,
            backbone_id=backbone_id,
            backbone_version=backbone_version,
            insertion_region_id=insertion_region_id,
            insertion_start=insertion_start,
            insertion_end=insertion_end,
            assembly_method="direct_insertion",
            revision_number=revision_number,
        )
        if not assembly.ok:
            readiness = evaluate_readiness(
                design,
                assembly_report=assembly.report,
            )
            return {
                "ok": False,
                "assembly": assembly.to_dict(),
                "plan": None,
                "readiness": readiness.to_dict(),
            }
        plan = create_assembly_plan(
            assembly,
            backbone,
            method=method,
            insertion_start=insertion_start,
            insertion_end=insertion_end,
            restriction_enzymes=restriction_enzymes,
            gibson_overlap_length=gibson_overlap_length,
            golden_gate_enzyme=golden_gate_enzyme,
            golden_gate_overhangs=golden_gate_overhangs,
        )
        readiness = evaluate_readiness(
            design,
            assembly_report=assembly.report,
            assembly_plan=plan,
        )
        return {
            "ok": not plan.blockers,
            "assembly": assembly.to_dict(),
            "plan": plan.to_dict(),
            "readiness": readiness.to_dict(),
        }


class AssemblyDeliverableService:
    def __init__(
        self,
        planning: AssemblyPlanningService,
        repository: JsonRepository,
        output_dir: Path,
    ):
        self.planning = planning
        self.repository = repository
        self.output_dir = output_dir

    def create(self, design_id: str, **request: Any) -> dict[str, Any]:
        revision_number = request.pop("revision_number", None)
        if revision_number is not None:
            try:
                revision_number = int(revision_number)
            except ValueError:
                revision_number = None
        primer_options = {
            key: request.pop(key)
            for key in list(request)
            if key.startswith("primer_")
        }
        planned = self.planning.plan(
            design_id,
            revision_number=revision_number,
            **request,
        )
        if not planned["ok"] or not planned["plan"]:
            return planned
        primers = design_assembly_primers(
            planned["plan"],
            **primer_options,
        ).to_dict()
        if revision_number is not None:
            design = self.planning.assemblies.designs.get_revision(design_id, revision_number)
        else:
            design = self.planning.assemblies.designs.get_v2(design_id)
        assert design is not None
        readiness = evaluate_readiness(
            design,
            assembly_report=planned["assembly"]["report"],
            assembly_plan=planned["plan"],
            primer_result=primers,
        )
        deliverable_id = f"assembly_delivery_{uuid4().hex[:12]}"
        payload = {
            "deliverable_id": deliverable_id,
            "ok": primers["status"] == "ready",
            "assembly": planned["assembly"],
            "plan": planned["plan"],
            "primers": primers,
            "readiness": readiness.to_dict(),
            "revision_number": revision_number or design.revision.revision_number,
            "revision_id": design.revision.revision_id,
            "source_context": {
                "design_id": design.design_id,
                "revision_id": design.revision.revision_id,
                "revision_number": design.revision.revision_number,
                "provenance_ids": [item.id for item in design.provenance],
            },
        }
        artifacts = write_assembly_deliverables(
            self.output_dir / deliverable_id,
            payload,
        )
        payload["artifacts"] = artifacts
        self.repository.save(deliverable_id, payload)
        return payload

    def get(self, deliverable_id: str) -> dict[str, Any] | None:
        return self.repository.get(deliverable_id)

    def artifact(
        self,
        deliverable_id: str,
        artifact_key: str,
    ) -> tuple[Path, str] | None:
        payload = self.get(deliverable_id)
        artifacts = payload.get("artifacts") if payload else None
        metadata = (
            artifacts.get(artifact_key)
            if isinstance(artifacts, dict)
            else None
        )
        if not isinstance(metadata, dict):
            return None
        base = (self.output_dir / deliverable_id).resolve()
        path = (base / str(metadata.get("filename") or "")).resolve()
        if base not in path.parents or not path.is_file():
            return None
        return path, str(metadata.get("media_type") or "application/octet-stream")


class SequenceQualityService:
    def __init__(
        self,
        designs: DesignService,
        host_profiles: HostProfileRegistryService,
    ):
        self.designs = designs
        self.host_profiles = host_profiles

    def analyze(
        self,
        design_id: str,
        *,
        part_ids: list[str] | None = None,
        window_size: int = 50,
        homopolymer_threshold: int = 6,
        repeat_length: int = 12,
    ) -> dict[str, Any]:
        design = self.designs.get_v2(design_id)
        if design is None:
            raise KeyError(design_id)
        return analyze_design_sequences(
            design,
            part_ids=part_ids,
            window_size=window_size,
            homopolymer_threshold=homopolymer_threshold,
            repeat_length=repeat_length,
        ).to_dict()

    def evaluate_optimization(
        self,
        design_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        design = self.designs.get_v2(design_id)
        if design is None:
            raise KeyError(design_id)
        optimization_request = SequenceOptimizationRequest(
            design_id=design_id,
            objective=str(
                request.get("objective") or "sequence_quality_baseline"
            ),
            host_profile_id=_optional_string(request.get("host_profile_id")),
            part_ids=list(request.get("part_ids") or []),
            optimized_sequences=dict(request.get("optimized_sequences") or {}),
            constraints=dict(request.get("constraints") or {}),
            dry_run=bool(request.get("dry_run", True)),
        )
        results = evaluate_sequence_optimization(design, optimization_request)
        statuses = [result.status for result in results]
        if any(status == "blocked" for status in statuses):
            status = "blocked"
        elif any(status == "needs_review" for status in statuses):
            status = "needs_review"
        else:
            status = "passed"
        return {
            "status": status,
            "design_id": design_id,
            "objective": optimization_request.objective,
            "host_profile_id": optimization_request.host_profile_id,
            "result_count": len(results),
            "results": [result.to_dict() for result in results],
            "schema_version": "1.0.0",
        }

    def create_optimized_revision(
        self,
        design_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        original = self.designs.get_v2(design_id)
        if original is None:
            raise KeyError(design_id)
        profile_id = str(request.get("host_profile_id") or "ecoli_k12_default")
        host_profile = self.host_profiles.get(profile_id)
        if host_profile is None:
            raise ValueError(f"Unknown host profile: {profile_id}")
        part_ids = list(request.get("part_ids") or [])
        generated = generate_host_optimized_sequences(
            original,
            host_profile,
            part_ids=part_ids,
        )
        optimization_request = SequenceOptimizationRequest(
            design_id=design_id,
            objective=str(request.get("objective") or "codon_optimization"),
            host_profile_id=host_profile.profile_id,
            part_ids=part_ids,
            optimized_sequences=generated,
            constraints={
                **dict(request.get("constraints") or {}),
                "protein_sequence": "preserve",
                "forbidden_motifs": list(host_profile.forbidden_motifs),
            },
            dry_run=False,
        )
        results = evaluate_sequence_optimization(original, optimization_request)
        if not results:
            raise ValueError("No CDS parts were available for optimization.")
        status = _optimization_rollup([result.status for result in results])
        if status == "blocked":
            readiness = evaluate_readiness(
                original,
                sequence_optimization_result={
                    "status": "blocked",
                    "results": [result.to_dict() for result in results],
                },
            )
            return {
                "ok": False,
                "status": "blocked",
                "design": original.to_dict(),
                "optimization": {
                    "status": "blocked",
                    "results": [result.to_dict() for result in results],
                    "schema_version": "1.0.0",
                },
                "diff": None,
                "readiness": readiness.to_dict(),
            }
        revised = _apply_sequence_optimization_revision(
            original,
            host_profile=host_profile,
            results=results,
            created_by=str(request.get("created_by") or "sequence_optimizer"),
        )
        saved = self.designs.save_v2(revised)
        diff = compare_designs(
            design_ir_from_dict(design_ir_v2_to_v1_payload(original.to_dict())),
            design_ir_from_dict(design_ir_v2_to_v1_payload(saved.to_dict())),
        )
        optimization_payload = {
            "status": status,
            "design_id": design_id,
            "host_profile_id": host_profile.profile_id,
            "objective": optimization_request.objective,
            "result_count": len(results),
            "results": [result.to_dict() for result in results],
            "schema_version": "1.0.0",
        }
        readiness = evaluate_readiness(
            saved,
            sequence_optimization_result=optimization_payload,
        )
        return {
            "ok": True,
            "status": status,
            "design": saved.to_dict(),
            "optimization": optimization_payload,
            "diff": asdict(diff),
            "readiness": readiness.to_dict(),
        }


class HostOptimizationService:
    def __init__(
        self,
        designs: DesignService,
        host_profiles: HostProfileRegistryService,
        repository: JsonRepository,
    ):
        self.designs = designs
        self.host_profiles = host_profiles
        self.repository = repository

    def rank_candidates(
        self,
        design_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        design = self.designs.get_v2(design_id)
        if design is None:
            raise KeyError(design_id)
        profile_id = str(request.get("host_profile_id") or "ecoli_k12_default")
        profile = self.host_profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"Unknown host profile: {profile_id}")
        result = rank_host_optimization_candidates(
            design,
            profile,
            part_ids=list(request.get("part_ids") or []),
            objective_weights=dict(request.get("objective_weights") or {}),
        )
        payload = result.to_dict()
        readiness = evaluate_readiness(
            design,
            host_optimization_result=payload,
        )
        return {
            "ok": result.status == "ready",
            "optimization": payload,
            "readiness": readiness.to_dict(),
        }

    def calibrate(self, request: dict[str, Any]) -> dict[str, Any]:
        design_id = str(request.get("design_id") or "")
        if self.designs.get_v2(design_id) is None:
            raise KeyError(design_id)
        profile_id = _optional_string(request.get("host_profile_id"))
        if profile_id and self.host_profiles.get(profile_id) is None:
            raise ValueError(f"Unknown host profile: {profile_id}")
        measurements = [
            measurement_from_dict(item)
            for item in list(request.get("measurements") or [])
            if isinstance(item, dict)
        ]
        if not measurements:
            raise ValueError("At least one experimental measurement is required.")
        result = summarize_host_calibration(
            calibration_id=_optional_string(request.get("calibration_id")),
            design_id=design_id,
            host_profile_id=profile_id,
            measurements=measurements,
        )
        payload = result.to_dict()
        self.repository.save(result.calibration_id, payload)
        return payload

    def get_calibration(self, calibration_id: str) -> dict[str, Any] | None:
        payload = self.repository.get(calibration_id)
        return calibration_from_dict(payload).to_dict() if payload else None

    def list_calibrations(self) -> dict[str, Any]:
        items = [calibration_from_dict(payload).to_dict() for payload in self.repository.list()]
        return {"items": items, "count": len(items)}


class OptimizationWorkflowService:
    def __init__(
        self,
        designs: DesignService,
        sequence_quality: SequenceQualityService,
        host_optimization: HostOptimizationService,
    ):
        self.designs = designs
        self.sequence_quality = sequence_quality
        self.host_optimization = host_optimization

    def run(
        self,
        design_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        original = self.designs.get_v2(design_id)
        if original is None:
            raise KeyError(design_id)
        part_ids = list(request.get("part_ids") or [])
        host_profile_id = str(request.get("host_profile_id") or "ecoli_k12_default")
        analysis = self.sequence_quality.analyze(
            design_id,
            part_ids=part_ids,
        )
        sequence_revision = self.sequence_quality.create_optimized_revision(
            design_id,
            {
                "host_profile_id": host_profile_id,
                "part_ids": part_ids,
                "objective": str(request.get("sequence_objective") or "codon_optimization"),
                "constraints": dict(request.get("constraints") or {}),
                "created_by": str(request.get("created_by") or "optimization_workflow"),
            },
        )
        if not sequence_revision["ok"]:
            return {
                "ok": False,
                "status": "blocked",
                "design_id": design_id,
                "steps": {
                    "sequence_analysis": analysis,
                    "sequence_optimization": sequence_revision,
                    "host_optimization": None,
                },
                "readiness": sequence_revision["readiness"],
            }
        optimized_design = self.designs.get_v2(design_id)
        assert optimized_design is not None
        host_candidates = self.host_optimization.rank_candidates(
            design_id,
            {
                "host_profile_id": host_profile_id,
                "part_ids": part_ids,
                "objective_weights": dict(request.get("objective_weights") or {}),
            },
        )
        sequence_optimization_result = sequence_revision["optimization"]
        host_optimization_result = host_candidates["optimization"]
        readiness = evaluate_readiness(
            optimized_design,
            sequence_optimization_result=sequence_optimization_result,
            host_optimization_result=host_optimization_result,
        )
        return {
            "ok": True,
            "status": "completed",
            "design_id": design_id,
            "host_profile_id": host_profile_id,
            "current_revision": optimized_design.revision.revision_number,
            "steps": {
                "sequence_analysis": analysis,
                "sequence_optimization": sequence_revision,
                "host_optimization": host_candidates,
            },
            "readiness": readiness.to_dict(),
            "limitations": list(host_optimization_result.get("limitations") or []),
        }


class RunService:
    def __init__(self, run_store: RunStore, settings: SettingsService | None = None):
        self.run_store = run_store
        self.settings = settings

    def start(self, request: dict[str, Any]) -> dict[str, Any]:
        req_model = _optional_string(request.get("model_name"))
        req_base = _optional_string(request.get("api_base"))
        req_cello_cmd = _optional_string(request.get("cello_command"))
        req_ucf_path = _optional_string(request.get("ucf_path"))
        req_sensor_path = _optional_string(request.get("sensor_path"))
        req_device_path = _optional_string(request.get("device_path"))
        req_host = _optional_string(request.get("host_organism"))
        req_budget = request.get("compute_budget")

        host = req_host or "Escherichia coli"
        budget = int(req_budget) if req_budget is not None else 6

        kwargs = {
            "user_intent": str(request.get("user_intent") or ""),
            "host_organism": host,
            "compute_budget": budget,
            "enable_rag": bool(request.get("enable_rag", True)),
            "enable_ode": bool(request.get("enable_ode", True)),
            "enable_skill_extraction": bool(
                request.get("enable_skill_extraction", True)
            ),
            "monte_carlo_samples": int(request.get("monte_carlo_samples") or 1),
            "model_name": req_model,
            "api_base": req_base,
            "cello_command": req_cello_cmd,
            "ucf_path": req_ucf_path,
            "sensor_path": req_sensor_path,
            "device_path": req_device_path,
            "run_store": self.run_store,
        }

        if self.settings:
            raw_settings = self.settings.get_settings_raw()
            if not req_model and raw_settings.get("model_name"):
                kwargs["model_name"] = raw_settings["model_name"]
            if not req_base and raw_settings.get("api_base"):
                kwargs["api_base"] = raw_settings["api_base"]
            if raw_settings.get("api_key"):
                kwargs["api_key"] = raw_settings["api_key"]
            if not req_cello_cmd and raw_settings.get("cello_command"):
                kwargs["cello_command"] = raw_settings["cello_command"]
            if not req_ucf_path and raw_settings.get("ucf_path"):
                kwargs["ucf_path"] = raw_settings["ucf_path"]
            if not req_sensor_path and raw_settings.get("sensor_path"):
                kwargs["sensor_path"] = raw_settings["sensor_path"]
            if not req_device_path and raw_settings.get("device_path"):
                kwargs["device_path"] = raw_settings["device_path"]
            if not req_host and raw_settings.get("default_host"):
                kwargs["host_organism"] = raw_settings["default_host"]
            if req_budget is None and raw_settings.get("default_compute_budget"):
                kwargs["compute_budget"] = int(raw_settings["default_compute_budget"])

        return start_design_run(**kwargs)

    def status(self, run_id: str) -> dict[str, Any]:
        return get_design_run_status(_validated_run_id(run_id), run_store=self.run_store)

    def events(
        self,
        run_id: str,
        *,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        return get_design_run_events(
            _validated_run_id(run_id),
            after_event_id=after_event_id,
            limit=limit,
            run_store=self.run_store,
        )

    def result(self, run_id: str) -> dict[str, Any]:
        return get_design_run_result(_validated_run_id(run_id), run_store=self.run_store)

    def list(self, limit: int = 20) -> dict[str, Any]:
        return list_design_runs(limit=limit, run_store=self.run_store)

    def cancel(self, run_id: str) -> dict[str, Any]:
        return cancel_design_run(_validated_run_id(run_id), run_store=self.run_store)

    def artifacts(self, run_id: str) -> dict[str, Any]:
        return get_design_run_artifacts(_validated_run_id(run_id), run_store=self.run_store)

    def submit_feedback(
        self,
        run_id: str,
        constraints: list[str] | str,
        *,
        action: str = "repair",
        extra_budget: int = 2,
    ) -> dict[str, Any]:
        return submit_design_feedback(
            _validated_run_id(run_id),
            constraints,
            action=action,
            extra_budget=extra_budget,
            run_store=self.run_store,
        )

    def resume(
        self,
        run_id: str,
        *,
        model_name: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        req_model = model_name
        req_base = api_base
        req_key = api_key

        kwargs = {
            "run_id": _validated_run_id(run_id),
            "run_store": self.run_store,
        }

        if self.settings:
            raw_settings = self.settings.get_settings_raw()
            if not req_model and raw_settings.get("model_name"):
                req_model = raw_settings["model_name"]
            if not req_base and raw_settings.get("api_base"):
                req_base = raw_settings["api_base"]
            if not req_key and raw_settings.get("api_key"):
                req_key = raw_settings["api_key"]

        if req_model is not None:
            kwargs["model_name"] = req_model
        if req_base is not None:
            kwargs["api_base"] = req_base
        if req_key is not None:
            kwargs["api_key"] = req_key

        return resume_design_run(**kwargs)


@dataclass
class ApplicationServices:
    imports: ImportService
    designs: DesignService
    comparisons: ComparisonService
    evaluations: EvaluationService
    simulations: SimulationService
    research: ResearchService
    exports: ExportService
    backbones: BackboneRegistryService
    host_profiles: HostProfileRegistryService
    plasmid_assemblies: PlasmidAssemblyService
    assembly_plans: AssemblyPlanningService
    assembly_deliverables: AssemblyDeliverableService
    sequence_quality: SequenceQualityService
    host_optimization: HostOptimizationService
    optimization_workflows: OptimizationWorkflowService
    runs: RunService
    settings: SettingsService
    design_drafts: DesignDraftService
    notifications: NotificationService

    @property
    def storage_backend(self) -> str:
        return repository_backend(self.designs.repository)


def create_application_services(
    base_dir: str | Path | None = None,
) -> ApplicationServices:
    selected = Path(base_dir) if base_dir else DEFAULT_API_DATA_DIR
    draft_repository = JsonRepository(selected / "drafts")
    design_draft_repository = JsonRepository(selected / "design_drafts")
    benchmark_repository = JsonRepository(selected / "benchmark_runs")
    parameter_fit_repository = JsonRepository(selected / "parameter_fit_snapshots")
    resource_calibration_repository = JsonRepository(
        selected / "resource_calibrations"
    )
    backbone_repository = JsonRepository(selected / "backbones")
    host_profile_repository = JsonRepository(selected / "host_profiles")
    host_calibration_repository = JsonRepository(selected / "host_calibrations")
    deliverable_repository = JsonRepository(selected / "assembly_deliverables")
    design_repository = create_design_repository(selected / "research.db")
    designs = DesignService(design_repository)
    _migrate_legacy_design_repository(selected / "designs", designs)
    run_store = RunStore(base_dir=selected / "runs")
    host_profiles = HostProfileRegistryService(host_profile_repository)
    simulation_service = SimulationService(parameter_fit_repository, host_profiles)
    backbones = BackboneRegistryService(backbone_repository)
    plasmid_assemblies = PlasmidAssemblyService(designs, backbones)
    assembly_plans = AssemblyPlanningService(plasmid_assemblies, backbones)
    sequence_quality = SequenceQualityService(designs, host_profiles)
    host_optimization = HostOptimizationService(
        designs,
        host_profiles,
        host_calibration_repository,
    )
    research_run_store = RunStore(
        base_dir=selected / "research_runs",
        max_workers=2,
    )
    settings_service = SettingsService(selected / "settings.json")
    return ApplicationServices(
        imports=ImportService(draft_repository, designs),
        designs=designs,
        comparisons=ComparisonService(designs),
        evaluations=EvaluationService(
            benchmark_repository,
            parameter_fit_repository,
            selected / "benchmark_reports",
            resource_calibration_repository,
        ),
        simulations=simulation_service,
        research=ResearchService(
            designs=designs,
            simulations=simulation_service,
            run_store=research_run_store,
            report_dir=selected / "research_reports",
        ),
        exports=ExportService(designs),
        backbones=backbones,
        host_profiles=host_profiles,
        plasmid_assemblies=plasmid_assemblies,
        assembly_plans=assembly_plans,
        assembly_deliverables=AssemblyDeliverableService(
            assembly_plans,
            deliverable_repository,
            selected / "assembly_delivery_files",
        ),
        sequence_quality=sequence_quality,
        host_optimization=host_optimization,
        optimization_workflows=OptimizationWorkflowService(
            designs,
            sequence_quality,
            host_optimization,
        ),
        runs=RunService(run_store, settings=settings_service),
        settings=settings_service,
        design_drafts=DesignDraftService(design_draft_repository),
        notifications=NotificationService(run_store, selected / "notifications_read_state.json"),
    )


def _validate_host_profile(profile: HostProfile) -> None:
    if not profile.profile_id.strip():
        raise ValueError("Host profile_id is required.")
    if not profile.name.strip():
        raise ValueError("Host profile name is required.")
    if not profile.host_organism.strip():
        raise ValueError("Host organism is required.")
    if not profile.codon_usage:
        raise ValueError("Host profile codon_usage is required.")


def _optimization_rollup(statuses: list[str]) -> str:
    if any(status == "blocked" for status in statuses):
        return "blocked"
    if any(status == "needs_review" for status in statuses):
        return "needs_review"
    return "passed"


def _apply_sequence_optimization_revision(
    design: DesignIRV2,
    *,
    host_profile: HostProfile,
    results: list[Any],
    created_by: str,
) -> DesignIRV2:
    revised = deepcopy(design)
    result_by_part = {result.part_id: result for result in results}
    created_at = datetime.now(timezone.utc).isoformat()
    provenance_id = f"sequence_optimization_{host_profile.profile_id}"
    revised.provenance.append(
        ProvenanceRecordV2(
            id=provenance_id,
            source_type="host_profile",
            source_uri=host_profile.source,
            source_version=host_profile.version,
            generated_by="sequence_optimizer",
            generated_at=created_at,
            metadata={
                "host_profile_id": host_profile.profile_id,
                "host_organism": host_profile.host_organism,
                "evidence_level": host_profile.evidence_level,
            },
        )
    )
    changes: list[dict[str, Any]] = []
    for part in revised.parts:
        result = result_by_part.get(part.id)
        if result is None or result.optimized_sequence is None:
            continue
        before = part.sequence
        part.sequence = result.optimized_sequence
        part.source = "sequence_optimized"
        if provenance_id not in part.provenance_ids:
            part.provenance_ids.append(provenance_id)
        part.metadata = {
            **dict(part.metadata or {}),
            "sequence_optimization": {
                "status": result.status,
                "host_profile_id": host_profile.profile_id,
                "objective": result.objective,
                "original_checksum": result.original_checksum,
                "optimized_checksum": result.optimized_checksum,
                "protein_preserved": result.protein_preserved,
                "change_count": len(result.changes),
            },
        }
        changes.append(
            {
                "operation": "optimize_sequence",
                "part_id": part.id,
                "before_checksum": result.original_checksum,
                "after_checksum": result.optimized_checksum,
                "change_count": len(result.changes),
                "before_sequence": before,
                "after_sequence": part.sequence,
            }
        )
    status = _optimization_rollup([result.status for result in results])
    revised.validation_status = {
        **dict(revised.validation_status or {}),
        "sequence_optimization": status,
        "sequences": _sequence_coverage_v2(revised),
    }
    revised.extensions = {
        **dict(revised.extensions or {}),
        "sequence_optimization": {
            "status": status,
            "host_profile_id": host_profile.profile_id,
            "result_count": len(results),
        },
    }
    parent_revision = revised.revision
    revised.revision = DesignRevisionV2(
        revision_id=f"revision_{parent_revision.revision_number + 1}",
        parent_revision_id=parent_revision.revision_id,
        revision_number=parent_revision.revision_number + 1,
        created_at=created_at,
        created_by=created_by,
        change_type="sequence_optimization",
        summary=(
            f"Optimized {len(changes)} CDS sequence(s) using "
            f"{host_profile.profile_id}."
        ),
        changes=changes,
    )
    if status == "needs_review":
        revised.warnings = list(
            dict.fromkeys(
                revised.warnings
                + [
                    "Sequence optimization completed with warnings; review "
                    "before treating the design as sequence optimized."
                ]
            )
        )
    return revised


def _sequence_coverage_v2(design: DesignIRV2) -> str:
    if not design.parts:
        return "missing"
    count = sum(1 for part in design.parts if part.sequence)
    if not count:
        return "missing"
    if count == len(design.parts):
        return "complete"
    return "partial"


@lru_cache(maxsize=1)
def get_default_services() -> ApplicationServices:
    return create_application_services()


def _validated_run_id(run_id: str) -> str:
    selected = str(run_id or "").strip()
    if not SAFE_RUN_ID.fullmatch(selected):
        raise ValueError("Invalid run ID.")
    return selected


def _migrate_legacy_design_repository(
    legacy_dir: Path,
    designs: DesignService,
) -> None:
    if not legacy_dir.exists():
        return
    legacy = JsonRepository(legacy_dir)
    for payload in legacy.list():
        design_id = str(payload.get("design_id") or "").strip()
        if not design_id or designs.repository.exists(design_id):
            continue
        designs.save(design_ir_from_dict(payload))
