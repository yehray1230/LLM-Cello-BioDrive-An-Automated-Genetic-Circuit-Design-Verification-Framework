from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status

from api.dependencies import get_services
from api.schemas import (
    BenchmarkComparisonRequest,
    BenchmarkRunRequest,
    ComparisonRequest,
    EvaluationRequest,
    GenBankImportRequest,
    ImportDraftRequest,
    JsonImportRequest,
    ParameterFitRequest,
    ParameterFitSnapshotComparisonRequest,
    ResourceCalibrationWorkflowRequest,
    ResourceModelAnalysisRequest,
    RunFeedbackRequest,
    RunResumeRequest,
    RunStartRequest,
    SimulationRequest,
    SimulationComparisonRequest,
    ParameterSweepRequest,
    BifurcationSweepRequest,
    SettingsUpdateRequest,
    SettingsTestRequest,
    DesignDraftUpdateRequest,
    ElicitationProposeRequest,
    temporal_inputs_to_dict,
)
from application.services import ApplicationServices
from exporters.claim_boundary import claim_boundary_headers, is_exchange_export_format
from mcp_server.service import list_tool_capabilities
from repositories.json_repository import RepositoryError
from schemas.import_draft import import_draft_from_json
from utils.llm_utils import resolve_agent_model


router = APIRouter(prefix="/api/v1")


def envelope(data: Any, warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "data": data,
        "meta": {"api_version": "v1", "schema_version": "1.0"},
        "warnings": warnings or [],
    }


@router.get("/health")
def health() -> dict[str, Any]:
    return envelope({"status": "ok", "service": "genetic-circuit-api"})


@router.get("/tool-capabilities")
def get_tool_capabilities() -> dict[str, Any]:
    return envelope(list_tool_capabilities())


@router.post("/imports/drafts/validate")
def validate_draft(
    request: ImportDraftRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    draft = import_draft_from_json(request.model_dump())
    return envelope(services.imports.validate(draft))


@router.post("/imports/json", status_code=status.HTTP_201_CREATED)
def import_json(
    request: JsonImportRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        draft = services.imports.import_json(request.draft.model_dump())
    except (ValueError, RepositoryError) as exc:
        raise _bad_request("IMPORT_INVALID", str(exc)) from exc
    validation = services.imports.validate(draft)
    return envelope(
        {"draft": draft.to_dict(), "validation": validation},
        validation["warnings"],
    )


@router.post("/imports/genbank", status_code=status.HTTP_201_CREATED)
def import_genbank(
    request: GenBankImportRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        draft = services.imports.import_genbank(
            request.content,
            filename=request.filename,
        )
    except (ValueError, RepositoryError) as exc:
        raise _bad_request("GENBANK_IMPORT_INVALID", str(exc)) from exc
    validation = services.imports.validate(draft)
    return envelope(
        {"draft": draft.to_dict(), "validation": validation},
        validation["warnings"],
    )


@router.post("/imports/{draft_id}/confirm", status_code=status.HTTP_201_CREATED)
def confirm_import(
    draft_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        design = services.imports.confirm_by_id(draft_id)
    except KeyError as exc:
        raise _not_found("DRAFT_NOT_FOUND", draft_id) from exc
    except (ValueError, RepositoryError) as exc:
        raise _bad_request("IMPORT_VALIDATION_FAILED", str(exc)) from exc
    return envelope(design.to_dict(), design.warnings)


@router.get("/designs")
def list_designs(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    designs = services.designs.list()
    return envelope(
        {
            "items": [design.to_dict() for design in designs],
            "count": len(designs),
        }
    )


@router.get("/designs/{design_id}")
def get_design(
    design_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        design = services.designs.get(design_id)
    except RepositoryError as exc:
        raise _bad_request("INVALID_DESIGN_ID", str(exc)) from exc
    if design is None:
        raise _not_found("DESIGN_NOT_FOUND", design_id)
    return envelope(design.to_dict(), design.warnings)


@router.get("/designs/{design_id}/ir-v2")
def get_design_ir_v2(
    design_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        design = services.designs.get_v2(design_id)
    except RepositoryError as exc:
        raise _bad_request("INVALID_DESIGN_ID", str(exc)) from exc
    if design is None:
        raise _not_found("DESIGN_NOT_FOUND", design_id)
    return envelope(
        design.to_dict(),
        ["This endpoint exposes the DesignIR v2 storage contract."],
    )


@router.get("/designs/{design_id}/simulation-spec")
def get_design_simulation_spec(
    design_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        spec = services.designs.simulation_spec(design_id)
    except RepositoryError as exc:
        raise _bad_request("INVALID_DESIGN_ID", str(exc)) from exc
    if spec is None:
        raise _not_found("DESIGN_NOT_FOUND", design_id)
    return envelope(
        spec,
        ["This is a computational simulation configuration, not an experimental protocol."],
    )


@router.get("/designs/{design_id}/revisions")
def list_design_revisions(
    design_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        if services.designs.get_v2(design_id) is None:
            raise _not_found("DESIGN_NOT_FOUND", design_id)
        revisions = services.designs.revisions(design_id)
    except RepositoryError as exc:
        raise _bad_request("INVALID_DESIGN_ID", str(exc)) from exc
    return envelope({"items": revisions, "count": len(revisions)})


@router.post("/comparisons")
def compare(
    request: ComparisonRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.comparisons.compare(
            request.left_design_id,
            request.right_design_id,
            left_metrics=request.left_metrics,
            right_metrics=request.right_metrics,
        )
    except KeyError as exc:
        raise _not_found("DESIGN_NOT_FOUND", str(exc.args[0])) from exc
    except RepositoryError as exc:
        raise _bad_request("INVALID_DESIGN_ID", str(exc)) from exc
    return envelope(result)


@router.post("/evaluations")
def evaluate(
    request: EvaluationRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.evaluations.evaluate(
            request.candidate,
            profile_id=request.profile_id,
        )
    except ValueError as exc:
        raise _bad_request("SCORING_PROFILE_INVALID", str(exc)) from exc
    return envelope(result)


@router.get("/evaluation/profiles")
def list_evaluation_profiles(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    profiles = services.evaluations.profiles()
    return envelope({"items": profiles, "count": len(profiles)})


@router.get("/simulation/models")
def list_simulation_models(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    models = services.simulations.models()
    return envelope({"items": models, "count": len(models)})


@router.post("/simulations")
def simulate_candidate(
    request: SimulationRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        topology = request.topology
        if request.parameter_fit_snapshot_id:
            snapshot = services.evaluations.parameter_fit_snapshot(
                request.parameter_fit_snapshot_id
            )
            if not snapshot:
                raise ValueError(
                    f"Parameter fit snapshot '{request.parameter_fit_snapshot_id}' not found."
                )
            topology = services.simulations.apply_parameter_fit_snapshot(
                topology, snapshot
            )
        result = services.simulations.simulate(
            topology,
            simulation_time=request.simulation_time,
            sample_count=request.sample_count,
            monte_carlo_samples=request.monte_carlo_samples,
            noise_fraction=request.noise_fraction,
            random_seed=request.random_seed,
            host_profile_id=request.host_profile_id,
            temporal_inputs=temporal_inputs_to_dict(request.temporal_inputs),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _bad_request("SIMULATION_INVALID", str(exc)) from exc
    return envelope(
        result,
        ["Simulation outputs are screening estimates and require experimental validation."],
    )


@router.post("/simulations/compare-snapshot")
def compare_simulation_snapshot(
    request: SimulationComparisonRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.simulations.compare_default_vs_fitted(
            request.topology,
            request.parameter_fit_snapshot_id,
            simulation_time=request.simulation_time,
            sample_count=request.sample_count,
            monte_carlo_samples=request.monte_carlo_samples,
            noise_fraction=request.noise_fraction,
            random_seed=request.random_seed,
            host_profile_id=request.host_profile_id,
            temporal_inputs=temporal_inputs_to_dict(request.temporal_inputs),
        )
    except KeyError as exc:
        raise _not_found("PARAMETER_FIT_NOT_FOUND", str(exc.args[0])) from exc
    except (TypeError, ValueError) as exc:
        raise _bad_request("SIMULATION_INVALID", str(exc)) from exc
    return envelope(
        result,
        ["Simulation comparisons are computational estimates and require experimental validation."],
    )


@router.post("/simulations/sweep")
def parameter_sweep(
    request: ParameterSweepRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    from tools.sensitivity_analysis import run_parameter_sweep
    try:
        result = run_parameter_sweep(
            request.topology,
            request.parameter_name,
            request.sweep_values,
            host_profile_id=request.host_profile_id,
            host_profiles=services.simulations.host_profiles,
        )
    except Exception as exc:
        raise _bad_request("SWEEP_INVALID", str(exc)) from exc
    return envelope(result)


@router.post("/simulations/bifurcation")
def bifurcation_sweep(
    request: BifurcationSweepRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    from tools.sensitivity_analysis import run_bifurcation_sweep
    try:
        result = run_bifurcation_sweep(
            request.topology,
            request.input_name,
            request.input_values,
            host_profile_id=request.host_profile_id,
            host_profiles=services.simulations.host_profiles,
        )
    except Exception as exc:
        raise _bad_request("SWEEP_INVALID", str(exc)) from exc
    return envelope(result)


@router.get("/benchmarks/datasets")
def list_benchmark_dataset_records(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    datasets = services.evaluations.datasets()
    return envelope({"items": datasets, "count": len(datasets)})


@router.get("/benchmarks/datasets/{dataset_id}")
def get_benchmark_dataset(
    dataset_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        dataset = services.evaluations.dataset(dataset_id)
    except KeyError as exc:
        raise _not_found("BENCHMARK_DATASET_NOT_FOUND", dataset_id) from exc
    except ValueError as exc:
        raise _bad_request("BENCHMARK_DATASET_INVALID", str(exc)) from exc
    return envelope(dataset)


@router.post("/benchmarks/runs", status_code=status.HTTP_201_CREATED)
def run_benchmark(
    request: BenchmarkRunRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.evaluations.run_benchmark(
            request.dataset_id,
            profile_id=request.profile_id,
        )
    except KeyError as exc:
        raise _not_found(
            "BENCHMARK_DATASET_NOT_FOUND",
            request.dataset_id,
        ) from exc
    except (ValueError, RepositoryError) as exc:
        raise _bad_request("BENCHMARK_RUN_INVALID", str(exc)) from exc
    return envelope(result)


@router.get("/benchmarks/runs")
def list_benchmark_runs(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    runs = services.evaluations.benchmark_results()
    return envelope({"items": runs, "count": len(runs)})


@router.get("/benchmarks/runs/{benchmark_run_id}")
def get_benchmark_run(
    benchmark_run_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.evaluations.benchmark_result(benchmark_run_id)
    except RepositoryError as exc:
        raise _bad_request("BENCHMARK_RUN_ID_INVALID", str(exc)) from exc
    if result is None:
        raise _not_found("BENCHMARK_RUN_NOT_FOUND", benchmark_run_id)
    return envelope(result)


@router.post("/benchmarks/comparisons")
def compare_benchmark_results(
    request: BenchmarkComparisonRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.evaluations.compare_benchmarks(
            request.benchmark_run_ids
        )
    except KeyError as exc:
        raise _not_found("BENCHMARK_RUN_NOT_FOUND", str(exc.args[0])) from exc
    except (ValueError, RepositoryError) as exc:
        raise _bad_request("BENCHMARK_COMPARISON_INVALID", str(exc)) from exc
    return envelope(result)


@router.post("/benchmarks/parameter-fits", status_code=status.HTTP_201_CREATED)
def create_parameter_fit_snapshot(
    request: ParameterFitRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.evaluations.create_parameter_fit_snapshot(
            request.model_dump()
        )
    except (ValueError, RepositoryError) as exc:
        raise _bad_request("PARAMETER_FIT_INVALID", str(exc)) from exc
    return envelope(result, [warning["message"] for warning in result.get("warnings", [])])


@router.get("/benchmarks/parameter-fits")
def list_parameter_fit_snapshots(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    items = services.evaluations.parameter_fit_snapshots()
    return envelope({"items": items, "count": len(items)})


@router.get("/benchmarks/parameter-fits/{snapshot_id}")
def get_parameter_fit_snapshot(
    snapshot_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.evaluations.parameter_fit_snapshot(snapshot_id)
    except RepositoryError as exc:
        raise _bad_request("PARAMETER_FIT_ID_INVALID", str(exc)) from exc
    if result is None:
        raise _not_found("PARAMETER_FIT_NOT_FOUND", snapshot_id)
    warnings = [
        warning["message"]
        for warning in result.get("warnings", [])
        if isinstance(warning, dict) and warning.get("message")
    ]
    return envelope(result, warnings)


@router.post("/benchmarks/parameter-fits/{snapshot_id}/comparison")
def create_parameter_fit_snapshot_comparison(
    snapshot_id: str,
    request: ParameterFitSnapshotComparisonRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.simulations.compare_default_vs_fitted(
            request.topology,
            snapshot_id,
            simulation_time=request.simulation_time,
            sample_count=request.sample_count,
            monte_carlo_samples=request.monte_carlo_samples,
            noise_fraction=request.noise_fraction,
            random_seed=request.random_seed,
            host_profile_id=request.host_profile_id,
            temporal_inputs=temporal_inputs_to_dict(request.temporal_inputs),
        )
    except KeyError as exc:
        raise _not_found("PARAMETER_FIT_NOT_FOUND", str(exc.args[0])) from exc
    except (TypeError, ValueError) as exc:
        raise _bad_request("SIMULATION_INVALID", str(exc)) from exc
    return envelope(
        result,
        [
            "Snapshot comparison reports are computational estimates and require experimental validation."
        ],
    )


@router.post("/resource-calibrations", status_code=status.HTTP_201_CREATED)
def create_resource_calibration_workflow(
    request: ResourceCalibrationWorkflowRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.evaluations.create_resource_calibration_workflow(
            request.model_dump()
        )
    except (RuntimeError, ValueError, RepositoryError) as exc:
        raise _bad_request("RESOURCE_CALIBRATION_INVALID", str(exc)) from exc
    return envelope(result, list(result.get("warnings") or []))


@router.get("/resource-calibrations")
def list_resource_calibration_workflows(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    items = services.evaluations.resource_calibration_workflows()
    return envelope({"items": items, "count": len(items)})


@router.get("/resource-calibrations/{workflow_id}")
def get_resource_calibration_workflow(
    workflow_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.evaluations.resource_calibration_workflow(workflow_id)
    except RepositoryError as exc:
        raise _bad_request("RESOURCE_CALIBRATION_ID_INVALID", str(exc)) from exc
    if result is None:
        raise _not_found("RESOURCE_CALIBRATION_NOT_FOUND", workflow_id)
    return envelope(result, list(result.get("warnings") or []))


@router.post("/resource-calibrations/{workflow_id}/model-analysis")
def analyze_resource_calibration_workflow(
    workflow_id: str,
    request: ResourceModelAnalysisRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.evaluations.analyze_resource_calibration_workflow(
            workflow_id,
            request.model_dump(),
        )
    except KeyError as exc:
        raise _not_found("RESOURCE_CALIBRATION_NOT_FOUND", workflow_id) from exc
    except (RuntimeError, ValueError, RepositoryError) as exc:
        raise _bad_request("RESOURCE_MODEL_ANALYSIS_INVALID", str(exc)) from exc
    return envelope(result, list(result.get("morris", {}).get("warnings") or []))


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
def start_run(
    request: RunStartRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    result = services.runs.start(request.model_dump())
    _raise_run_error(result)
    return envelope(result, list(result.get("warnings") or []))


@router.get("/runs")
def list_runs(
    limit: int = 20,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    result = services.runs.list(limit=limit)
    _raise_run_error(result)
    return envelope(result)


@router.get("/runs/{run_id}")
def get_run(
    run_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.runs.status(run_id)
    except ValueError as exc:
        raise _bad_request("INVALID_RUN_ID", str(exc)) from exc
    _raise_run_error(result)
    return envelope(result)


@router.get("/runs/{run_id}/events")
def get_run_events(
    run_id: str,
    after_event_id: int = 0,
    limit: int = 100,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.runs.events(
            run_id,
            after_event_id=after_event_id,
            limit=limit,
        )
    except ValueError as exc:
        raise _bad_request("INVALID_RUN_ID", str(exc)) from exc
    _raise_run_error(result)
    return envelope(result)


@router.get("/runs/{run_id}/result")
def get_run_result(
    run_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.runs.result(run_id)
    except ValueError as exc:
        raise _bad_request("INVALID_RUN_ID", str(exc)) from exc
    _raise_run_error(result)
    return envelope(result)


@router.get("/runs/{run_id}/artifacts")
def get_run_artifacts(
    run_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.runs.artifacts(run_id)
    except ValueError as exc:
        raise _bad_request("INVALID_RUN_ID", str(exc)) from exc
    _raise_run_error(result)
    return envelope(result)


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.runs.cancel(run_id)
    except ValueError as exc:
        raise _bad_request("INVALID_RUN_ID", str(exc)) from exc
    _raise_run_error(result)
    return envelope(result)


@router.post("/runs/{run_id}/feedback")
def submit_run_feedback(
    run_id: str,
    request: RunFeedbackRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.runs.submit_feedback(
            run_id,
            request.constraints,
            action=request.action,
            extra_budget=request.extra_budget,
        )
    except ValueError as exc:
        raise _bad_request("INVALID_RUN_ID", str(exc)) from exc
    _raise_run_error(result)
    return envelope(result)


@router.post("/runs/{run_id}/resume", status_code=status.HTTP_202_ACCEPTED)
def resume_run(
    run_id: str,
    request: RunResumeRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.runs.resume(
            run_id,
            model_name=request.model_name,
            api_base=request.api_base,
        )
    except ValueError as exc:
        raise _bad_request("INVALID_RUN_ID", str(exc)) from exc
    _raise_run_error(result)
    return envelope(result)


@router.get("/designs/{design_id}/exports/{export_format}")
def export_design(
    design_id: str,
    export_format: str,
    rev: int | None = None,
    backbone: str | None = None,
    services: ApplicationServices = Depends(get_services),
) -> Response:
    try:
        result = services.exports.export(
            design_id,
            export_format,
            revision_number=rev,
            backbone_name=backbone,
        )
    except KeyError as exc:
        raise _not_found("DESIGN_NOT_FOUND", design_id) from exc
    except (ValueError, RepositoryError) as exc:
        raise _bad_request("EXPORT_INVALID", str(exc)) from exc
    if not result.ok:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "EXPORT_BLOCKED",
                "message": result.status,
                "details": result.errors,
                "warnings": result.warnings,
            },
        )
    headers = {
        "Content-Disposition": f'attachment; filename="{result.filename}"',
        "X-Export-Status": result.status,
        "X-Export-Warning-Count": str(len(result.warnings)),
        "X-Safety-Status": result.safety_status,
        "X-Safety-Review-Required": str(result.safety_review_required).lower(),
    }
    if is_exchange_export_format(export_format):
        headers.update(claim_boundary_headers())
    source = (
        services.designs.get_revision(design_id, rev)
        if rev is not None
        else services.designs.get_v2(design_id)
    )
    if source is not None:
        headers["X-Source-Revision"] = str(source.revision.revision_number)
        headers["X-Source-Revision-Id"] = source.revision.revision_id
    return Response(
        content=result.content,
        media_type=result.media_type,
        headers=headers,
    )


def _bad_request(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": code, "message": message, "details": []},
    )


def _not_found(code: str, identifier: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": code,
            "message": f"Resource not found: {identifier}",
            "details": [],
        },
    )


def _raise_run_error(result: dict[str, Any]) -> None:
    result_status = str(result.get("status") or "")
    error_type = str(result.get("error_type") or "")
    if result_status == "not_found" or error_type == "not_found":
        raise _not_found(
            "RUN_NOT_FOUND",
            str(result.get("run_id") or "unknown"),
        )
    if result_status == "error":
        raise _bad_request(
            error_type.upper() or "RUN_ERROR",
            str(result.get("error") or "Run operation failed."),
        )


# ==========================================
# Phase 1 & 2 Settings, Drafts, Notifications API Routes
# ==========================================

@router.get("/settings")
def get_settings(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return envelope(services.settings.get_settings_masked())


@router.post("/settings")
def save_settings(
    request: SettingsUpdateRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    services.settings.save_settings(request.model_dump())
    return envelope(services.settings.get_settings_masked())


@router.get("/settings/status")
def get_settings_status(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return envelope(services.settings.check_availability())


@router.post("/settings/test")
def test_settings_connection(
    request: SettingsTestRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return envelope(services.settings.check_availability(request.model_dump()))


@router.delete("/settings/api-key")
def clear_settings_api_key(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    services.settings.clear_api_key()
    return envelope(services.settings.get_settings_masked())


@router.get("/designs/drafts/active")
def get_active_design_draft(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return envelope(services.design_drafts.get_active())


@router.post("/designs/drafts")
def save_design_draft(
    request: DesignDraftUpdateRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    from utils.safety_checker import check_safety, log_safety_event
    intent = request.user_intent or ""
    host = request.host_organism or ""
    warnings_list = []
    if intent:
        safety_result = check_safety(intent, host)
        if not safety_result.is_safe:
            log_safety_event(
                None,
                intent,
                safety_result.status,
                safety_result.warnings,
                action="save_design_draft",
                categories=safety_result.categories,
            )
            raise _bad_request("SAFETY_VIOLATION", safety_result.redirection_message)
        if safety_result.warnings:
            warnings_list.extend(safety_result.warnings)
            log_safety_event(
                None,
                intent,
                safety_result.status,
                safety_result.warnings,
                action="save_design_draft",
                categories=safety_result.categories,
            )
    return envelope(services.design_drafts.save(request.model_dump()), warnings=warnings_list)


@router.delete("/designs/drafts/active")
def clear_active_design_draft(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    services.design_drafts.clear()
    return envelope({"status": "cleared"})


@router.post("/designs/drafts/elicitation/next")
def next_elicitation(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    draft = services.design_drafts.get_active()
    if not draft:
        raise HTTPException(status_code=400, detail="No active draft found.")

    from schemas.state import DesignState
    from agents.pm_agent import call_pm_agent

    state = DesignState(
        user_intent=draft.get("user_intent") or "",
        host_organism=draft.get("host_organism") or "Escherichia coli",
        compute_budget=draft.get("compute_budget") or 6,
        structured_spec=draft.get("structured_spec") or {},
        pm_chat_history=draft.get("pm_chat_history") or [],
        pending_proposal=draft.get("pending_proposal") or {},
        pm_stage=draft.get("pm_stage") or "elicitation",
    )

    if not state.pending_proposal or not state.pm_chat_history:
        settings = services.settings.get_settings_raw()
        api_key = settings.get("api_key")
        model_name = settings.get("model_name") or "gpt-4o-mini"
        api_base = settings.get("api_base")

        state = call_pm_agent(
            state,
            api_key=api_key,
            model_name=resolve_agent_model("pm", model_name),
            api_base=api_base,
        )

    updated_draft = services.design_drafts.save({
        "structured_spec": state.structured_spec,
        "pm_chat_history": state.pm_chat_history,
        "pending_proposal": state.pending_proposal,
        "pm_stage": state.pm_stage,
    })
    return envelope(updated_draft)


@router.post("/designs/drafts/elicitation/propose")
def propose_elicitation(
    request: ElicitationProposeRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    draft = services.design_drafts.get_active()
    if not draft:
        raise HTTPException(status_code=400, detail="No active draft found.")

    from schemas.state import DesignState
    from agents.pm_agent import call_pm_agent
    import json

    state = DesignState(
        user_intent=draft.get("user_intent") or "",
        host_organism=draft.get("host_organism") or "Escherichia coli",
        compute_budget=draft.get("compute_budget") or 6,
        structured_spec=draft.get("structured_spec") or {},
        pm_chat_history=draft.get("pm_chat_history") or [],
        pending_proposal=draft.get("pending_proposal") or {},
        pm_stage=draft.get("pm_stage") or "elicitation",
    )

    if not state.pending_proposal:
        raise HTTPException(status_code=400, detail="No pending proposal to reply to.")

    missing_field = state.pending_proposal["missing_field"]
    proposed_value = state.pending_proposal["proposed_value"]

    if request.choice == "agree":
        state.structured_spec[missing_field] = proposed_value
        state.pm_chat_history.append({
            "role": "user",
            "content": f"同意使用推薦值：{json.dumps(proposed_value, ensure_ascii=False)}",
        })
        state.pm_chat_history.append({
            "role": "assistant",
            "content": f"已儲存 {missing_field} 設定。",
        })
        state.pending_proposal = {}
    elif request.choice == "override":
        custom_val = request.value
        if isinstance(custom_val, str):
            custom_val = custom_val.strip()
            try:
                parsed_val = json.loads(custom_val)
            except Exception:
                if "," in custom_val:
                    parsed_val = [x.strip() for x in custom_val.split(",")]
                else:
                    parsed_val = custom_val
        else:
            parsed_val = custom_val

        state.structured_spec[missing_field] = parsed_val
        state.pm_chat_history.append({
            "role": "user",
            "content": f"我想要改為：{json.dumps(parsed_val, ensure_ascii=False) if not isinstance(parsed_val, str) else parsed_val}",
        })
        state.pm_chat_history.append({
            "role": "assistant",
            "content": f"已自訂 {missing_field} 為: {json.dumps(parsed_val, ensure_ascii=False)}。",
        })
        state.pending_proposal = {}

    settings = services.settings.get_settings_raw()
    api_key = settings.get("api_key")
    model_name = settings.get("model_name") or "gpt-4o-mini"
    api_base = settings.get("api_base")

    state = call_pm_agent(
        state,
        api_key=api_key,
        model_name=resolve_agent_model("pm", model_name),
        api_base=api_base,
    )

    updated_draft = services.design_drafts.save({
        "structured_spec": state.structured_spec,
        "pm_chat_history": state.pm_chat_history,
        "pending_proposal": state.pending_proposal,
        "pm_stage": state.pm_stage,
    })
    return envelope(updated_draft)


@router.post("/designs/drafts/elicitation/skip")
def skip_elicitation(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    draft = services.design_drafts.get_active()
    if not draft:
        raise HTTPException(status_code=400, detail="No active draft found.")

    fallbacks = {
        "chassis": "Escherichia coli",
        "copy_number": 15,
    }
    structured_spec = draft.get("structured_spec") or {}
    for k, v in fallbacks.items():
        if k not in structured_spec:
            structured_spec[k] = v

    pm_chat_history = draft.get("pm_chat_history") or []
    pm_chat_history.append({
        "role": "user",
        "content": "略過對話，直接套用大腸桿菌預設規格。",
    })
    pm_chat_history.append({
        "role": "assistant",
        "content": "已套用預設規格，可直接啟動設計。",
    })

    updated_draft = services.design_drafts.save({
        "structured_spec": structured_spec,
        "pm_chat_history": pm_chat_history,
        "pending_proposal": {},
        "pm_stage": "completed",
    })
    return envelope(updated_draft)


@router.get("/notifications")
def get_notifications(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    notifications = [n.to_dict() for n in services.notifications.get_notifications()]
    unread_count = services.notifications.get_unread_count()
    return envelope({
        "notifications": notifications,
        "unread_count": unread_count
    })


@router.post("/notifications/{notification_id}/read")
def mark_notification_as_read(
    notification_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    services.notifications.mark_as_read(notification_id)
    return envelope({"status": "success"})


@router.post("/notifications/read-all")
def mark_all_notifications_as_read(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    services.notifications.mark_all_as_read()
    return envelope({"status": "success"})


@router.get("/designs/{design_id}/revisions/compare")
def compare_revisions(
    design_id: str,
    left: int,
    right: int,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.comparisons.compare_revisions(design_id, left, right)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return envelope(result)
