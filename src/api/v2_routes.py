from __future__ import annotations

from typing import Any

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from api.dependencies import get_services
from api.downloads import assembly_artifact_file_response
from api.v2_schemas import (
    AssemblyDeliverableRequest,
    AssemblyPlanRequest,
    BackboneRegistrationRequest,
    HostProfileRegistrationRequest,
    HostCalibrationRequest,
    HostOptimizationCandidateRequest,
    PlasmidAssemblyRequest,
    OptimizationWorkflowRequest,
    ResearchComparisonRequest,
    ResearchSimulationRequest,
    SequenceAnalysisRequest,
    SequenceOptimizationEvaluationRequest,
    SequenceOptimizationRevisionRequest,
)
from application.services import ApplicationServices


router = APIRouter(prefix="/api/v2")


def envelope(data: Any, warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "data": data,
        "meta": {"api_version": "v2", "schema_version": "2.0"},
        "warnings": warnings or [],
    }


_INTERNAL_PATH_FIELDS = {
    "run_dir",
    "workflow_run_dir",
    "result_path",
    "run_manifest_path",
    "async_run_dir",
}


def _sanitize_research_payload(data: Any, run_id: str | None = None) -> Any:
    if isinstance(data, list):
        return [_sanitize_research_payload(item, run_id=run_id) for item in data]
    if not isinstance(data, dict):
        return data

    selected_run_id = str(data.get("run_id") or data.get("research_run_id") or run_id or "")
    sanitized: dict[str, Any] = {}
    for key, value in data.items():
        if key in _INTERNAL_PATH_FIELDS:
            continue
        if key == "artifacts" and isinstance(value, dict):
            sanitized["artifact_keys"] = sorted(str(item) for item in value)
            if selected_run_id:
                sanitized["artifact_links"] = {
                    str(item): f"/api/v2/research/runs/{selected_run_id}/artifacts/{item}"
                    for item in sorted(value)
                }
            continue
        if key == "runs" and isinstance(value, list):
            sanitized[key] = [_sanitize_research_payload(item) for item in value]
            continue
        sanitized[key] = _sanitize_research_payload(value, run_id=selected_run_id or run_id)
    return sanitized


@router.get("/health")
def health(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return envelope(
        {
            "status": "ok",
            "service": "genetic-circuit-research-api",
            "storage_backend": services.storage_backend,
        }
    )


@router.post("/designs/{design_id}/plasmid-assemblies")
def assemble_plasmid(
    design_id: str,
    request: PlasmidAssemblyRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.plasmid_assemblies.assemble(
            design_id,
            plasmid_id=request.plasmid_id,
            backbone_id=request.backbone_id,
            backbone_version=request.backbone_version,
            insertion_region_id=request.insertion_region_id,
            insertion_start=request.insertion_start,
            insertion_end=request.insertion_end,
            assembly_method=request.assembly_method,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Design not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.ok:
        raise HTTPException(
            status_code=409,
            detail={
                "message": result.report.status,
                "report": result.report.to_dict(),
            },
        )
    return envelope(result.to_dict())


@router.post("/backbones", status_code=status.HTTP_201_CREATED)
def register_backbone(
    request: BackboneRegistrationRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        entry = services.backbones.register(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return envelope(entry.to_dict())


@router.get("/backbones")
def list_backbones(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    entries = services.backbones.list()
    return envelope(
        {
            "items": [entry.to_dict() for entry in entries],
            "count": len(entries),
        }
    )


@router.get("/backbones/{backbone_id}/versions/{version}")
def get_backbone(
    backbone_id: str,
    version: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    entry = services.backbones.get(backbone_id, version)
    if entry is None:
        raise HTTPException(status_code=404, detail="Backbone not found.")
    return envelope(entry.to_dict())


@router.get("/host-profiles")
def list_host_profiles(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    profiles = services.host_profiles.list()
    return envelope(
        {
            "items": [profile.to_dict() for profile in profiles],
            "count": len(profiles),
        }
    )


@router.get("/host-profiles/{profile_id}")
def get_host_profile(
    profile_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    profile = services.host_profiles.get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Host profile not found.")
    return envelope(profile.to_dict())


@router.post("/host-profiles", status_code=status.HTTP_201_CREATED)
def register_host_profile(
    request: HostProfileRegistrationRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        profile = services.host_profiles.register(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return envelope(profile.to_dict())


@router.post("/designs/{design_id}/sequence-analysis")
def analyze_design_sequences(
    design_id: str,
    request: SequenceAnalysisRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.sequence_quality.analyze(
            design_id,
            **request.model_dump(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Design not found.") from exc
    return envelope(result)


@router.post("/designs/{design_id}/sequence-optimization/evaluate")
def evaluate_design_sequence_optimization(
    design_id: str,
    request: SequenceOptimizationEvaluationRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.sequence_quality.evaluate_optimization(
            design_id,
            request.model_dump(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Design not found.") from exc
    return envelope(result)


@router.post("/designs/{design_id}/sequence-optimization/revisions")
def create_design_sequence_optimization_revision(
    design_id: str,
    request: SequenceOptimizationRevisionRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.sequence_quality.create_optimized_revision(
            design_id,
            request.model_dump(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Design not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result["ok"]:
        raise HTTPException(status_code=409, detail=result)
    return envelope(result)


@router.post("/designs/{design_id}/host-optimization/candidates")
def rank_design_host_optimization_candidates(
    design_id: str,
    request: HostOptimizationCandidateRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.host_optimization.rank_candidates(
            design_id,
            request.model_dump(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Design not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result["ok"]:
        raise HTTPException(status_code=409, detail=result)
    return envelope(result)


@router.post("/host-optimization/calibrations", status_code=status.HTTP_201_CREATED)
def create_host_calibration(
    request: HostCalibrationRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.host_optimization.calibrate(request.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Design not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return envelope(result)


@router.get("/host-optimization/calibrations")
def list_host_calibrations(
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return envelope(services.host_optimization.list_calibrations())


@router.get("/host-optimization/calibrations/{calibration_id}")
def get_host_calibration(
    calibration_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    result = services.host_optimization.get_calibration(calibration_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Host calibration not found.")
    return envelope(result)


@router.post("/designs/{design_id}/optimization-workflow")
def run_design_optimization_workflow(
    design_id: str,
    request: OptimizationWorkflowRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.optimization_workflows.run(
            design_id,
            request.model_dump(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Design not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result["ok"]:
        raise HTTPException(status_code=409, detail=result)
    return envelope(result)


@router.post("/designs/{design_id}/assembly-plans")
def create_plasmid_assembly_plan(
    design_id: str,
    request: AssemblyPlanRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.assembly_plans.plan(
            design_id,
            **request.model_dump(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Design not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result["ok"]:
        raise HTTPException(status_code=409, detail=result)
    return envelope(result)


@router.post("/designs/{design_id}/assembly-deliverables")
def create_assembly_deliverables(
    design_id: str,
    request: AssemblyDeliverableRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.assembly_deliverables.create(
            design_id,
            **request.model_dump(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Design not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result["ok"]:
        raise HTTPException(status_code=409, detail=result)
    return envelope(result)


@router.get("/assembly-deliverables/{deliverable_id}")
def get_assembly_deliverables(
    deliverable_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    result = services.assembly_deliverables.get(deliverable_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Assembly deliverable not found.")
    return envelope(result)


@router.get(
    "/assembly-deliverables/{deliverable_id}/artifacts/{artifact_key}"
)
def get_assembly_deliverable_artifact(
    deliverable_id: str,
    artifact_key: str,
    services: ApplicationServices = Depends(get_services),
) -> FileResponse:
    return assembly_artifact_file_response(
        services.assembly_deliverables,
        deliverable_id,
        artifact_key,
    )


@router.post("/research/runs", status_code=status.HTTP_202_ACCEPTED)
def start_research_run(
    request: ResearchSimulationRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.research.start_simulation(request.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Design not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return envelope(_sanitize_research_payload(result))


@router.get("/research/runs")
def list_research_runs(
    limit: int = 50,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    return envelope(_sanitize_research_payload(services.research.list(limit=limit)))


@router.get("/research/runs/{run_id}")
def get_research_run(
    run_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    result = services.research.status(run_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Research run not found.")
    return envelope(_sanitize_research_payload(result, run_id=run_id))


@router.get("/research/runs/{run_id}/result")
def get_research_result(
    run_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    result = services.research.result(run_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Research run not found.")
    return envelope(_sanitize_research_payload(result, run_id=run_id))


@router.get("/research/runs/{run_id}/artifacts/{artifact_key}")
def get_research_artifact(
    run_id: str,
    artifact_key: str,
    services: ApplicationServices = Depends(get_services),
) -> FileResponse:
    result = services.research.result(run_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Research run not found.")
    artifacts = result.get("artifacts")
    path_value = artifacts.get(artifact_key) if isinstance(artifacts, dict) else None
    if not path_value:
        raise HTTPException(status_code=404, detail="Research artifact not found.")
    path = Path(str(path_value)).resolve()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Research artifact not found.")
    return FileResponse(path, filename=path.name)


@router.post("/research/runs/{run_id}/cancel")
def cancel_research_run(
    run_id: str,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    result = services.research.cancel(run_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Research run not found.")
    return envelope(_sanitize_research_payload(result, run_id=run_id))


@router.post("/research/comparisons")
def compare_research_runs(
    request: ResearchComparisonRequest,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, Any]:
    try:
        result = services.research.compare(request.research_run_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return envelope(result)
