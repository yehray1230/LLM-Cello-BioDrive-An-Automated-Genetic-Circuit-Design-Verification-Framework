from __future__ import annotations

from pathlib import Path
import json
import re
import logging
import shutil
from typing import Annotated, Any
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates

from api.dependencies import get_services
from api.downloads import assembly_artifact_file_response
from application.services import ApplicationServices
from exporters.claim_boundary import (
    claim_boundary_json,
    claim_boundary_markdown,
    claim_boundary_payload,
)
from schemas.import_draft import FieldEvidence, ImportDraft
from schemas.workflow_evidence import (
    is_valid_ode_trace as _valid_ode_trace,
    project_ode_trace_rows,
)


logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)
TERMINAL_RUN_STATUSES = {
    "completed",
    "needs_human_input",
    "error",
    "failed",
    "cancelled",
}


@router.get("/", response_class=HTMLResponse)
@router.get("/web", response_class=HTMLResponse)
def dashboard(
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    designs = services.designs.list()
    run_data = services.runs.list(limit=5)
    runs = run_data.get("runs", []) if isinstance(run_data, dict) else []

    from tools.cello_wrapper import CelloWrapper
    from tools.tool_adapters import CelloLogicSynthesisAdapter
    settings = services.settings.get_settings_masked()
    cello_cmd = settings.get("cello_command") or None
    wrapper = CelloWrapper(
        cello_command=cello_cmd,
        cello_artifact_format=settings.get("cello_artifact_format") or "cello_v2",
        ucf_path=settings.get("ucf_path") or None,
        sensor_path=settings.get("sensor_path") or None,
        device_path=settings.get("device_path") or None,
    )
    adapter = CelloLogicSynthesisAdapter(wrapper=wrapper)
    try:
        cello_status = adapter.available().to_dict()
    except Exception as e:
        cello_status = {
            "status": "error",
            "version": None,
            "adapter_name": "cello_wrapper",
            "fallback_used": True,
            "warnings": [{"category": "ERROR", "message": str(e)}]
        }

    return _template(
        request,
        "dashboard.html",
        designs=designs,
        runs=runs,
        design_count=len(designs),
        active_run_count=sum(
            str(item.get("status")) not in TERMINAL_RUN_STATUSES
            for item in runs
        ),
        cello_status=cello_status,
    )


@router.get("/web/designs", response_class=HTMLResponse)
def designs_page(
    request: Request,
    q: str = "",
    host: str = "",
    status: str = "",
    show_archived: bool = False,
    show_deleted: bool = False,
    page: int = 1,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    from benchmark_suite.readiness_evaluator import evaluate_readiness

    # 1. Fetch v2 designs
    designs_v2 = services.designs.list_v2(show_archived=show_archived, show_deleted=show_deleted)

    # 2. Enrich and filter
    enriched = []
    for d in designs_v2:
        readiness = evaluate_readiness(d)
        host_org = d.biological_context.host_organism.value or ""

        # Search filter
        if q:
            q_lower = q.lower()
            if (
                q_lower not in d.name.lower()
                and q_lower not in d.design_id.lower()
                and not (d.specification.user_intent and q_lower in d.specification.user_intent.lower())
            ):
                continue

        # Host filter
        if host:
            if host.lower() not in host_org.lower():
                continue

        # Status filter
        if status:
            if status != readiness.readiness_status:
                continue

        enriched.append({
            "design_id": d.design_id,
            "name": d.name,
            "inputs": d.specification.inputs,
            "outputs": d.specification.outputs,
            "parts_count": len(d.parts),
            "host_organism": host_org or "Unknown",
            "readiness_status": readiness.readiness_status,
            "is_pinned": getattr(d, "is_pinned", False),
            "is_archived": getattr(d, "is_archived", False),
            "is_deleted": getattr(d, "is_deleted", False),
            "updated_at": d.revision.created_at or "Unknown",
        })

    # Sort pinned first
    enriched.sort(key=lambda x: x["is_pinned"], reverse=True)

    # Pagination
    page_size = 10
    total_count = len(enriched)
    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * page_size
    paginated = enriched[start_idx : start_idx + page_size]

    # Fetch all options for filters
    all_raw = services.designs.list_v2(show_archived=True, show_deleted=True)
    all_hosts = sorted(list({
        d.biological_context.host_organism.value
        for d in all_raw
        if d.biological_context.host_organism.value
    }))
    all_statuses = sorted(list({
        evaluate_readiness(d).readiness_status
        for d in all_raw
    }))

    return _template(
        request,
        "designs.html",
        designs=paginated,
        q=q,
        selected_host=host,
        selected_status=status,
        show_archived=show_archived,
        show_deleted=show_deleted,
        all_hosts=all_hosts,
        all_statuses=all_statuses,
        page=page,
        total_pages=total_pages,
        total_count=total_count,
    )


@router.post("/web/designs/{design_id}/pin")
def pin_design_route(
    design_id: str,
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    services.designs.pin(design_id)
    return RedirectResponse(f"/web/designs/{design_id}", status_code=303)


@router.post("/web/designs/{design_id}/unpin")
def unpin_design_route(
    design_id: str,
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    services.designs.unpin(design_id)
    return RedirectResponse(f"/web/designs/{design_id}", status_code=303)


@router.post("/web/designs/{design_id}/archive")
def archive_design_route(
    design_id: str,
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    services.designs.archive(design_id)
    return RedirectResponse("/web/designs", status_code=303)


@router.post("/web/designs/{design_id}/unarchive")
def unarchive_design_route(
    design_id: str,
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    services.designs.unarchive(design_id)
    return RedirectResponse(f"/web/designs/{design_id}", status_code=303)


@router.post("/web/designs/{design_id}/delete")
def delete_design_route(
    design_id: str,
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    services.designs.soft_delete(design_id)
    return RedirectResponse("/web/designs", status_code=303)


@router.post("/web/designs/{design_id}/restore")
def restore_design_route(
    design_id: str,
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    services.designs.restore(design_id)
    return RedirectResponse(f"/web/designs/{design_id}", status_code=303)


@router.get("/web/designs/{design_id}/delete_preview", response_class=HTMLResponse)
def delete_preview_page(
    design_id: str,
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    design = services.designs.get_v2(design_id)
    if not design:
        raise HTTPException(status_code=404, detail="Design not found.")

    revisions = services.designs.revisions(design_id) or []

    # Count related deliverables
    all_deliverables = services.assembly_deliverables.repository.list()
    matching_deliverables = [
        d for d in all_deliverables
        if d.get("assembly", {}).get("design_id") == design_id
        or d.get("source_context", {}).get("design_id") == design_id
    ]

    # Count associated runs
    related_runs = []
    try:
        research_runs = services.research.list(limit=100).get("runs", [])
        related_runs = [
            r for r in research_runs
            if _research_run_design_id(r) == design_id
        ]
    except (KeyError, TypeError, ValueError):
        logger.warning(
            "Unable to enumerate research runs for delete preview.",
            exc_info=True,
        )

    return _template(
        request,
        "delete_preview.html",
        design=design,
        revisions_count=len(revisions),
        deliverables_count=len(matching_deliverables),
        runs_count=len(related_runs),
    )


@router.post("/web/designs/{design_id}/purge")
def purge_design_route(
    design_id: str,
    understand: Annotated[bool, Form()],
    confirm_design_id: Annotated[str, Form()],
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    if not understand or confirm_design_id.strip() != design_id:
        raise HTTPException(
            status_code=403,
            detail="Destructive action confirmation did not match the design ID.",
        )

    # 1. Clean up associated assembly deliverables & files
    all_deliverables = services.assembly_deliverables.repository.list()
    matching_deliverables = [
        d for d in all_deliverables
        if d.get("assembly", {}).get("design_id") == design_id
        or d.get("source_context", {}).get("design_id") == design_id
    ]
    for deliverable in matching_deliverables:
        deliverable_id = deliverable.get("deliverable_id")
        if deliverable_id:
            services.assembly_deliverables.repository.delete(deliverable_id)
            deliverable_dir = (
                services.assembly_deliverables.output_dir / deliverable_id
            )
            if deliverable_dir.exists():
                shutil.rmtree(deliverable_dir)

    # 2. Delete design from repository
    purged = services.designs.purge(design_id)
    if not purged:
        raise HTTPException(status_code=404, detail="Design not found or could not be purged.")

    return RedirectResponse("/web/designs", status_code=303)


def _research_run_design_id(run: dict[str, Any]) -> str | None:
    """Return the explicit design association stored by a research run."""
    for container in (
        run,
        run.get("request"),
        run.get("input"),
        run.get("summary"),
        run.get("result"),
    ):
        if isinstance(container, dict) and container.get("design_id"):
            return str(container["design_id"])
    return None


@router.get("/web/assembly", response_class=HTMLResponse)
def assembly_workspace(
    request: Request,
    deliverable_id: str = "",
    services: ApplicationServices = Depends(get_services),
) -> Response:
    if deliverable_id:
        return RedirectResponse(
            f"/web/assembly/deliverables/{deliverable_id}",
            status_code=303,
        )
    return _assembly_template(request, services, "assembly.html")


@router.get("/web/assembly/backbones", response_class=HTMLResponse)
def assembly_backbones_page(
    request: Request,
    backbone: str = "",
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    return _assembly_template(
        request,
        services,
        "assembly_backbones.html",
        selected_backbone=backbone,
    )


@router.get("/web/assembly/new", response_class=HTMLResponse)
def assembly_new_page(
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    return _assembly_template(request, services, "assembly_new.html")


@router.post("/web/assembly/backbones")
async def upload_assembly_backbone(
    file: Annotated[UploadFile, File()],
    backbone_id: Annotated[str, Form()],
    version: Annotated[str, Form()],
    name: Annotated[str, Form()],
    source_uri: Annotated[str, Form()] = "",
    host_organisms: Annotated[str, Form()] = "Escherichia coli",
    origin_of_replication: Annotated[str, Form()] = "unknown",
    selection_marker: Annotated[str, Form()] = "unknown",
    copy_number_class: Annotated[str, Form()] = "unknown",
    insertion_region_id: Annotated[str, Form()] = "mcs",
    insertion_region_name: Annotated[str, Form()] = "Insertion region",
    insertion_start: Annotated[int, Form()] = 0,
    insertion_end: Annotated[int, Form()] = 1,
    essential_regions_json: Annotated[str, Form()] = "[]",
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    content = await file.read()
    if len(content) > 5_000_000:
        raise HTTPException(status_code=413, detail="Upload exceeds 5 MB.")
    try:
        essential_regions = json.loads(essential_regions_json or "[]")
        if not isinstance(essential_regions, list):
            raise ValueError("Essential regions must be a JSON array.")
        entry = services.backbones.register(
            {
                "backbone_id": backbone_id,
                "version": version,
                "name": name,
                "source_type": "user_upload",
                "source_uri": source_uri.strip()
                or f"upload://{file.filename or 'backbone.gb'}",
                "genbank": content.decode("utf-8-sig"),
                "host_organisms": _comma_list(host_organisms),
                "origin_of_replication": origin_of_replication,
                "selection_marker": selection_marker,
                "copy_number_class": copy_number_class,
                "insertion_regions": [
                    {
                        "region_id": insertion_region_id,
                        "name": insertion_region_name,
                        "start": insertion_start,
                        "end": insertion_end,
                        "description": "Registered from the HTML assembly workspace.",
                    }
                ],
                "essential_regions": essential_regions,
            }
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(
        f"/web/assembly/backbones?backbone={entry.backbone_id}@{entry.version}",
        status_code=303,
    )


@router.post("/web/assembly/deliverables")
def create_assembly_delivery(
    design_id: Annotated[str, Form()],
    plasmid_id: Annotated[str, Form()],
    backbone_id: Annotated[str, Form()],
    backbone_version: Annotated[str, Form()],
    insertion_region_id: Annotated[str, Form()],
    insertion_start: Annotated[int, Form()],
    insertion_end: Annotated[int, Form()],
    method: Annotated[str, Form()] = "gibson",
    restriction_enzymes: Annotated[str, Form()] = "EcoRI,BsaI,BsmBI",
    gibson_overlap_length: Annotated[int, Form()] = 25,
    golden_gate_enzyme: Annotated[str, Form()] = "BsaI",
    golden_gate_overhangs: Annotated[str, Form()] = "",
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    overhangs = _comma_list(golden_gate_overhangs)
    try:
        result = services.assembly_deliverables.create(
            design_id,
            plasmid_id=plasmid_id,
            backbone_id=backbone_id,
            backbone_version=backbone_version,
            insertion_region_id=insertion_region_id,
            insertion_start=insertion_start,
            insertion_end=insertion_end,
            method=method,
            restriction_enzymes=_comma_list(restriction_enzymes),
            gibson_overlap_length=gibson_overlap_length,
            golden_gate_enzyme=golden_gate_enzyme,
            golden_gate_overhangs=overhangs or None,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("deliverable_id"):
        raise HTTPException(status_code=409, detail=result)
    return RedirectResponse(
        f"/web/assembly/deliverables/{result['deliverable_id']}",
        status_code=303,
    )


@router.get(
    "/web/assembly/deliverables/{deliverable_id}",
    response_class=HTMLResponse,
)
def assembly_report_page(
    deliverable_id: str,
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    deliverable = services.assembly_deliverables.get(deliverable_id)
    if deliverable is None:
        raise HTTPException(status_code=404, detail="Assembly deliverable not found.")
    return _assembly_template(
        request,
        services,
        "assembly_report.html",
        deliverable=deliverable,
    )


@router.get(
    "/web/assembly/deliverables/{deliverable_id}/downloads",
    response_class=HTMLResponse,
)
def assembly_downloads_page(
    deliverable_id: str,
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    deliverable = services.assembly_deliverables.get(deliverable_id)
    if deliverable is None:
        raise HTTPException(status_code=404, detail="Assembly deliverable not found.")
    return _assembly_template(
        request,
        services,
        "assembly_downloads.html",
        deliverable=deliverable,
    )


@router.get(
    "/web/assembly/deliverables/{deliverable_id}/artifacts/{artifact_key}"
)
def download_assembly_delivery(
    deliverable_id: str,
    artifact_key: str,
    services: ApplicationServices = Depends(get_services),
) -> FileResponse:
    return assembly_artifact_file_response(
        services.assembly_deliverables,
        deliverable_id,
        artifact_key,
    )


@router.get("/web/benchmarks", response_class=HTMLResponse)
def benchmarks_page(
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    return _template(
        request,
        "benchmarks.html",
        profiles=services.evaluations.profiles(),
        datasets=services.evaluations.datasets(),
        runs=services.evaluations.benchmark_results(),
    )


@router.post("/web/benchmarks")
def start_benchmark(
    dataset_id: Annotated[str, Form()],
    profile_id: Annotated[str, Form()] = "research-v1.8",
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    try:
        result = services.evaluations.run_benchmark(
            dataset_id,
            profile_id=profile_id,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(
        f"/web/benchmarks/{result['benchmark_run_id']}",
        status_code=303,
    )


@router.get(
    "/web/benchmarks/{benchmark_run_id}",
    response_class=HTMLResponse,
)
def benchmark_detail(
    benchmark_run_id: str,
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    result = services.evaluations.benchmark_result(benchmark_run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Benchmark run not found.")
    return _template(request, "benchmark_detail.html", benchmark=result)


@router.get("/web/resource-calibrations", response_class=HTMLResponse)
def resource_calibrations_page(
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    return _template(
        request,
        "resource_calibrations.html",
        workflows=services.evaluations.resource_calibration_workflows(),
        workflow=None,
    )


@router.post("/web/resource-calibrations")
async def create_resource_calibration_page(
    bundle_file: Annotated[UploadFile, File()],
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    content = await bundle_file.read(2_000_001)
    if len(content) > 2_000_000:
        raise HTTPException(status_code=400, detail="Calibration bundle exceeds 2 MB.")
    try:
        payload = json.loads(content.decode("utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("Calibration bundle must contain one JSON object.")
        result = services.evaluations.create_resource_calibration_workflow(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(
        f"/web/resource-calibrations/{result['workflow_id']}",
        status_code=303,
    )


@router.get(
    "/web/resource-calibrations/{workflow_id}",
    response_class=HTMLResponse,
)
def resource_calibration_detail_page(
    workflow_id: str,
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    workflow = services.evaluations.resource_calibration_workflow(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Resource calibration not found.")
    return _template(
        request,
        "resource_calibrations.html",
        workflows=services.evaluations.resource_calibration_workflows(),
        workflow=workflow,
    )


@router.post("/web/resource-calibrations/{workflow_id}/model-analysis")
def resource_calibration_model_analysis_page(
    workflow_id: str,
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    try:
        services.evaluations.analyze_resource_calibration_workflow(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Resource calibration not found.") from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(
        f"/web/resource-calibrations/{workflow_id}",
        status_code=303,
    )


@router.get("/web/designs/{design_id}", response_class=HTMLResponse)
def design_detail(
    design_id: str,
    request: Request,
    rev: int | None = None,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    design = services.designs.get(design_id)
    if design is None:
        raise HTTPException(status_code=404, detail="Design not found.")

    from web.design_views import build_design_context_view
    view = build_design_context_view(design_id, rev, services)

    is_historical = rev is not None
    viewed_rev = rev if rev is not None else view.latest_rev

    return _template(
        request,
        "design_detail.html",
        design=design,
        design_v2=services.designs.get_v2(design_id),
        simulation_spec=services.designs.simulation_spec(design_id),
        revisions=services.designs.revisions(design_id),
        view=view,
        readiness=view.readiness,
        is_historical=is_historical,
        viewed_rev=viewed_rev,
    )


def _codon_optimization_context(
    design_id: str,
    services: ApplicationServices,
) -> dict[str, object]:
    design = services.designs.get_v2(design_id)
    if design is None:
        raise HTTPException(status_code=404, detail="Design not found.")
    analysis = services.sequence_quality.analyze(design_id)
    profiles = [profile.to_dict() for profile in services.host_profiles.list()]
    return {
        "design": design,
        "analysis": analysis,
        "profiles": profiles,
        "eligible_cds_count": sum(
            1
            for part in design.parts
            if str(part.part_type or "").upper() == "CDS" and part.sequence
        ),
    }


@router.get(
    "/web/designs/{design_id}/optimize/codon",
    response_class=HTMLResponse,
)
def codon_optimization_page(
    design_id: str,
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    return _template(
        request,
        "codon_optimization.html",
        **_codon_optimization_context(design_id, services),
    )


@router.post(
    "/web/designs/{design_id}/optimize/codon",
    response_class=HTMLResponse,
)
def create_codon_optimization_revision(
    design_id: str,
    request: Request,
    host_profile_id: Annotated[str, Form()] = "ecoli_k12_default",
    confirm: Annotated[str | None, Form()] = None,
    confirm_design_id: Annotated[str, Form()] = "",
    services: ApplicationServices = Depends(get_services),
) -> Response:
    context = _codon_optimization_context(design_id, services)
    if confirm != "on":
        return _template(
            request,
            "codon_optimization.html",
            **context,
            selected_host_profile_id=host_profile_id,
            error_message=_localized(
                request,
                "請勾選確認方塊後再建立最佳化 revision。",
                "Confirm the checkbox before creating an optimized revision.",
            ),
            status_code=422,
        )
    if confirm_design_id.strip() != design_id:
        return _template(
            request,
            "codon_optimization.html",
            **context,
            selected_host_profile_id=host_profile_id,
            error_message=_localized(
                request,
                "確認用的設計 ID 不相符。",
                "The confirmation design ID does not match.",
            ),
            status_code=403,
        )
    try:
        result = services.sequence_quality.create_optimized_revision(
            design_id,
            {
                "host_profile_id": host_profile_id,
                "objective": "codon_optimization",
                "created_by": "html_codon_optimizer",
            },
        )
    except ValueError:
        return _template(
            request,
            "codon_optimization.html",
            **context,
            selected_host_profile_id=host_profile_id,
            error_message=_localized(
                request,
                "此設計目前沒有可進行保守密碼子最佳化的 CDS。",
                "This design has no CDS eligible for conservative codon optimization.",
            ),
            status_code=400,
        )
    if not result.get("ok"):
        return _template(
            request,
            "codon_optimization.html",
            **context,
            selected_host_profile_id=host_profile_id,
            optimization=result,
            error_message=_localized(
                request,
                "安全檢查阻止了 revision 建立；原設計未變更。",
                "Safety checks blocked revision creation; the original design was unchanged.",
            ),
            status_code=409,
        )
    revision_number = result["design"]["revision"]["revision_number"]
    return RedirectResponse(
        f"/web/designs/{design_id}?rev={revision_number}#revisions",
        status_code=303,
    )


@router.get("/web/research", response_class=HTMLResponse)
def research_workspace(
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    run_data = services.research.list(limit=50)
    return _template(
        request,
        "research.html",
        designs=services.designs.list_v2(),
        runs=run_data.get("runs", []),
        models=services.simulations.models(),
        profiles=services.evaluations.profiles(),
    )


@router.post("/web/research/runs")
def start_research_run(
    design_id: Annotated[str, Form()] = "",
    verilog: Annotated[str, Form()] = "",
    truth_table_json: Annotated[str, Form()] = "",
    copy_number: Annotated[float, Form()] = 1.0,
    simulation_time: Annotated[float, Form()] = 600.0,
    sample_count: Annotated[int, Form()] = 80,
    monte_carlo_samples: Annotated[int, Form()] = 1,
    noise_fraction: Annotated[float, Form()] = 0.15,
    random_seed: Annotated[str, Form()] = "",
    profile_id: Annotated[str, Form()] = "research-v2-preview",
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    topology = None
    if verilog.strip():
        try:
            truth_table = (
                json.loads(truth_table_json)
                if truth_table_json.strip()
                else []
            )
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail="Truth table must be valid JSON.",
            ) from exc
        topology = {
            "verilog": verilog.strip(),
            "truth_table": truth_table,
            "copy_number": copy_number,
        }
    payload = {
        "design_id": design_id.strip() or None,
        "topology": topology,
        "simulation_time": simulation_time,
        "sample_count": sample_count,
        "monte_carlo_samples": monte_carlo_samples,
        "noise_fraction": noise_fraction,
        "random_seed": int(random_seed) if random_seed.strip() else None,
        "profile_id": profile_id,
    }
    try:
        result = services.research.start_simulation(payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(
        f"/web/research/runs/{result['run_id']}",
        status_code=303,
    )


@router.get("/web/research/runs/{run_id}", response_class=HTMLResponse)
def research_run_detail(
    run_id: str,
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    run = services.research.status(run_id)
    if run.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Research run not found.")
    terminal = run.get("status") in TERMINAL_RUN_STATUSES
    result = services.research.result(run_id) if terminal else None
    return _template(
        request,
        "research_run.html",
        run=run,
        result=result,
        terminal=terminal,
    )


@router.get("/web/research/compare", response_class=HTMLResponse)
def research_compare(
    request: Request,
    runs: str = "",
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    run_ids = [item.strip() for item in runs.split(",") if item.strip()]
    comparison = None
    error = None
    if len(run_ids) >= 2:
        try:
            comparison = services.research.compare(run_ids)
        except ValueError as exc:
            error = str(exc)
    return _template(
        request,
        "research_compare.html",
        runs=services.research.list(limit=50).get("runs", []),
        selected=run_ids,
        comparison=comparison,
        error=error,
    )


@router.get("/web/imports", response_class=HTMLResponse)
def imports_page(request: Request) -> HTMLResponse:
    return _template(request, "imports.html")


@router.post("/web/imports/guided")
def create_guided_import(
    name: Annotated[str, Form()],
    source_type: Annotated[str, Form()] = "literature",
    source_uri: Annotated[str, Form()] = "",
    citation: Annotated[str, Form()] = "",
    host_organism: Annotated[str, Form()] = "unknown",
    inputs: Annotated[str, Form()] = "",
    outputs: Annotated[str, Form()] = "",
    logic_expression: Annotated[str, Form()] = "",
    validation_status: Annotated[str, Form()] = "unknown",
    validation_notes: Annotated[str, Form()] = "",
    evidence_status: Annotated[str, Form()] = "unknown",
    locator: Annotated[str, Form()] = "",
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    draft = ImportDraft(
        draft_id=f"external_{uuid4().hex[:12]}",
        name=name.strip(),
        source_type=source_type.strip() or "literature",
        source_uri=source_uri.strip() or None,
        citation=citation.strip(),
        host_organism=host_organism.strip() or "unknown",
        inputs=_comma_list(inputs),
        outputs=_comma_list(outputs),
        logic_expression=logic_expression.strip(),
        validation_status=validation_status.strip() or "unknown",
        validation_notes=validation_notes.strip(),
        evidence=[
            FieldEvidence(
                field_path="design_summary",
                status=evidence_status,
                source_uri=source_uri.strip() or None,
                locator=locator.strip() or None,
            )
        ],
    )
    services.imports.save_draft(draft)
    return RedirectResponse(
        f"/web/imports/{draft.draft_id}",
        status_code=303,
    )


@router.post("/web/imports/upload")
async def upload_import(
    file: Annotated[UploadFile, File()],
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    content = await file.read()
    if len(content) > 5_000_000:
        raise HTTPException(status_code=413, detail="Upload exceeds 5 MB.")
    filename = file.filename or "external_design.json"
    try:
        if filename.lower().endswith(".json"):
            draft = services.imports.import_json(content)
        else:
            draft = services.imports.import_genbank(
                content,
                filename=filename,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(
        f"/web/imports/{draft.draft_id}",
        status_code=303,
    )


@router.get("/web/imports/{draft_id}", response_class=HTMLResponse)
def review_import(
    draft_id: str,
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    draft = services.imports.get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return _template(
        request,
        "import_review.html",
        draft=draft,
        validation=services.imports.validate(draft),
    )


@router.post("/web/imports/{draft_id}/confirm")
def confirm_import(
    draft_id: str,
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    try:
        design = services.imports.confirm_by_id(draft_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Draft not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(
        f"/web/designs/{design.design_id}",
        status_code=303,
    )


@router.get("/web/runs", response_class=HTMLResponse)
def runs_page(
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    result = services.runs.list(limit=50)
    return _template(
        request,
        "runs.html",
        runs=result.get("runs", []),
    )


@router.get("/web/new-design", response_class=HTMLResponse)
def new_design_page(
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    return _template(
        request,
        "new_design.html",
        settings=services.settings.get_settings_masked(),
    )


@router.post("/web/runs")
def start_run(
    user_intent: Annotated[str, Form()],
    host_organism: Annotated[str, Form()] = "Escherichia coli",
    compute_budget: Annotated[int, Form()] = 6,
    model_name: Annotated[str, Form()] = "",
    api_base: Annotated[str, Form()] = "",
    enable_rag: Annotated[str | None, Form()] = None,
    enable_ode: Annotated[str | None, Form()] = None,
    enable_skill_extraction: Annotated[str | None, Form()] = None,
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    settings = services.settings.get_settings_masked()
    if not settings.get("model_credentials_configured"):
        return RedirectResponse(
            "/web/settings?credentials_required=1",
            status_code=303,
        )
    result = services.runs.start(
        {
            "user_intent": user_intent,
            "host_organism": host_organism,
            "compute_budget": compute_budget,
            "model_name": model_name or None,
            "api_base": api_base or None,
            "enable_rag": enable_rag == "on",
            "enable_ode": enable_ode == "on",
            "enable_skill_extraction": enable_skill_extraction == "on",
        }
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error"))
    try:
        services.design_drafts.clear()
    except Exception:
        pass
    return RedirectResponse(
        f"/web/runs/{result['run_id']}",
        status_code=303,
    )


@router.get("/web/runs/{run_id}", response_class=HTMLResponse)
def run_detail(
    run_id: str,
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    try:
        status = services.runs.status(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid run ID.") from exc
    if status.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Run not found.")
    events = services.runs.events(run_id, limit=100).get("events", [])
    result = (
        services.runs.result(run_id)
        if status.get("status") in TERMINAL_RUN_STATUSES
        else None
    )
    return _template(
        request,
        "run_detail.html",
        run=status,
        events=events,
        result=result,
        monitor=_run_monitor_view(status, events, result),
        terminal=status.get("status") in TERMINAL_RUN_STATUSES,
    )


@router.get("/web/runs/{run_id}/status")
def run_monitor_status(
    run_id: str,
    after_event_id: int = 0,
    services: ApplicationServices = Depends(get_services),
) -> dict[str, object]:
    payload = _run_monitor_payload(
        run_id,
        services,
        after_event_id=after_event_id,
    )
    if payload["run"].get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Run not found.")
    return payload


@router.post("/web/runs/{run_id}/feedback")
def run_feedback(
    run_id: str,
    constraints: Annotated[str, Form()],
    action: Annotated[str, Form()] = "repair",
    extra_budget: Annotated[int, Form()] = 2,
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    result = services.runs.submit_feedback(
        run_id,
        constraints,
        action=action,
        extra_budget=extra_budget,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error"))
    return RedirectResponse(f"/web/runs/{run_id}", status_code=303)


@router.post("/web/runs/{run_id}/resume")
def run_resume(
    run_id: str,
    model_name: Annotated[str, Form()] = "",
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    result = services.runs.resume(
        run_id,
        model_name=model_name or None,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error"))
    return RedirectResponse(
        f"/web/runs/{result['run_id']}",
        status_code=303,
    )


@router.get("/web/compare", response_class=HTMLResponse)
def compare_page(
    request: Request,
    left: str | None = None,
    right: str | None = None,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    designs = services.designs.list()
    comparison = None
    error = None
    if left and right and left != right:
        try:
            comparison = services.comparisons.compare(left, right)
        except KeyError:
            error = "One of the selected designs no longer exists."
    return _template(
        request,
        "compare.html",
        designs=designs,
        left=left,
        right=right,
        comparison=comparison,
        error=error,
    )


GLOSSARY = {
    "設計資料庫": "Design Library",
    "已確認並持久化的外部或計算設計。": "Confirmed and persisted computational or external designs.",
    "名稱": "Name",
    "輸入": "Inputs",
    "輸出": "Outputs",
    "零件": "Parts",
    "驗證狀態": "Validation",
    "開啟模擬工作區": "Open Simulation Workspace",
    "生物情境": "Biological Context",
    "宿主生物": "Host Organism",
    "底盤": "Chassis",
    "假設數量": "Assumptions Count",
    "警告數量": "Warnings Count",
    "設計規格與評估": "Design Specification & Evaluation",
    "材料清單 (BoM)": "Bill of Materials (BoM)",
    "裝配與交付下載": "Assembly & Delivery Downloads",
    "匯出中心": "Export Center",
    "版本歷程": "Revision History",
    "最佳化": "Optimization",
    "邏輯設計規格": "Logic Design Specification",
    "邏輯表達式": "Logic Expression",
    "輸入信號": "Input Signals",
    "輸出信號": "Output Signals",
    "零件總數": "Total Parts",
    "未設定": "Not configured",
    "封存": "Archive",
    "取消封存": "Unarchive",
    "刪除": "Delete",
    "還原": "Restore",
    "永久刪除": "Purge",
    "釘選": "Pin",
    "取消釘選": "Unpin",
    "搜尋": "Search",
    "篩選": "Filter",
    "狀態": "Status",
    "最近瀏覽": "Recently Viewed",
    "成熟度": "Maturity",
    "分頁": "Pagination",
    "清除": "Clear",
    "確定": "Confirm",
    "取消": "Cancel",
    "封存設計": "Archive Design",
    "刪除影響預覽": "Delete Impact Preview",
    "關閉": "Close",
    "無資料": "No data",
    "儀表板": "Dashboard",
    "工作執行紀錄": "Runs",
    "外部設計匯入": "External Imports",
    "研究工作區": "Research Workspace",
    "裝配交付中心": "Assembly Delivery",
    "基準測試": "Benchmarks",
    "設定": "Settings",
    "API文件": "API Docs",
    "語系": "Language",
    "跳至主要內容": "Skip to main content",
    "輸入設計 ID 以確認": "Enter the design ID to confirm",
}


def _request_language(request: Request) -> str:
    lang = request.query_params.get("lang") or request.cookies.get("lang") or "zh-Hant"
    return lang if lang in {"zh-Hant", "en"} else "zh-Hant"


def _localized(request: Request, zh_hant: str, english: str) -> str:
    return english if _request_language(request) == "en" else zh_hant


def _template(
    request: Request,
    name: str,
    **context: object,
) -> Response:
    lang = _request_language(request)
    response_status_code = int(context.pop("status_code", 200))

    def _t(text: str) -> str:
        if lang == "en":
            return GLOSSARY.get(text, text)
        return text

    def _tr(zh_hant: str, english: str) -> str:
        return english if lang == "en" else zh_hant

    status_labels = {
        "queued": ("排隊中", "Queued"),
        "running": ("執行中", "Running"),
        "starting": ("啟動中", "Starting"),
        "simulation": ("模擬中", "Simulation"),
        "evaluation": ("評估中", "Evaluation"),
        "reporting": ("產生報告中", "Reporting"),
        "needs_human_input": ("等待人工回覆", "Awaiting human input"),
        "completed": ("已完成", "Completed"),
        "failed": ("失敗", "Failed"),
        "error": ("錯誤", "Error"),
        "cancelled": ("已取消", "Cancelled"),
        "ready": ("就緒", "Ready"),
        "primer_ready": ("引子已就緒", "Primer ready"),
        "blocked": ("受阻", "Blocked"),
        "preview": ("預覽", "Preview"),
    }
    job_kind_labels = {
        "design": ("設計", "Design"),
        "research": ("研究", "Research"),
    }
    enum_labels = {
        **status_labels,
        "concentration": ("濃度狀態模型", "Concentration state model"),
        "unknown": ("未知", "Unknown"),
        "low": ("低", "Low"),
        "medium": ("中", "Medium"),
        "high": ("高", "High"),
        "pcr": ("PCR 擴增", "PCR"),
        "direct_synthesis": ("直接合成", "Direct synthesis"),
        "forward": ("正向", "Forward"),
        "reverse": ("反向", "Reverse"),
        "gibson": ("Gibson 組裝", "Gibson assembly"),
        "golden_gate": ("Golden Gate 組裝", "Golden Gate assembly"),
        "restriction_cloning": ("限制酶選殖", "Restriction cloning"),
        "mapped": ("已映射", "Mapped"),
        "mapping_failed": ("映射失敗", "Mapping failed"),
        "simulated": ("已模擬", "Simulated"),
        "disabled": ("未啟用", "Disabled"),
        "provisional": ("暫定結果", "Provisional"),
        "fallback": ("備援結果", "Fallback"),
        "incomplete": ("不完整", "Incomplete"),
        "pass": ("通過", "Pass"),
        "fail": ("未通過", "Fail"),
        "functional": ("功能正確性", "Functional"),
        "kinetic": ("動力學", "Kinetic"),
        "static_plausibility": ("靜態合理性", "Static plausibility"),
        "metabolic_burden": ("代謝負擔", "Metabolic burden"),
        "robustness": ("穩健性", "Robustness"),
        "orthogonality": ("正交性", "Orthogonality"),
        "cello_assignment": ("Cello 元件指派", "Cello assignment"),
        "toxicity": ("毒性", "Toxicity"),
        "semantic_faithfulness": ("語意忠實度", "Semantic faithfulness"),
    }

    def _localized_label(value: object, labels: dict[str, tuple[str, str]]) -> str:
        normalized = str(value or "")
        lookup_key = normalized.lower()
        zh_hant, english = labels.get(
            lookup_key,
            (normalized.replace("_", " "), normalized.replace("_", " ").title()),
        )
        return english if lang == "en" else zh_hant

    def _candidate_limit_label(value: object) -> str:
        text = str(value or "")
        if lang != "en":
            return text
        exact = {
            "Cello 映射失敗 (UCF 限制不匹配或無可用邏輯閘)": (
                "Cello mapping failed (UCF constraints did not match or no logic gate was available)"
            ),
            "無明顯限制因素": "No obvious limiting factor",
        }
        if text in exact:
            return exact[text]
        match = re.fullmatch(r"(.+) 表現較差 \(([^)]+)\)", text)
        if match:
            return f"{match.group(1)} underperforms ({match.group(2)})"
        return text

    dynamic_copy = {
        "Synthetic, deterministic fixtures for validating score direction, evidence sensitivity, and comparison/report infrastructure.": (
            "用於驗證分數方向、證據敏感度，以及比較與報告基礎設施的合成確定性測試資料。"
        ),
    }

    def _domain_text(value: object) -> str:
        text = str(value or "")
        if lang == "zh-Hant":
            return dynamic_copy.get(text, text)
        return text

    from api.dependencies import get_services
    services = get_services()
    unread_notifications = 0
    running_jobs = []
    try:
        unread_notifications = len(services.notifications.list_unread())
    except Exception:
        pass
    try:
        all_runs = services.runs.list(limit=100).get("runs", [])
        running_jobs = [
            r for r in all_runs
            if r.get("status") not in TERMINAL_RUN_STATUSES
        ]
    except Exception:
        pass

    settings = {}
    try:
        settings = services.settings.get_settings_masked()
    except Exception:
        pass

    cello_status = None
    try:
        from tools.cello_wrapper import CelloWrapper
        from tools.tool_adapters import CelloLogicSynthesisAdapter
        cello_cmd = settings.get("cello_command") or None
        wrapper = CelloWrapper(
            cello_command=cello_cmd,
            cello_artifact_format=settings.get("cello_artifact_format") or "cello_v2",
            ucf_path=settings.get("ucf_path") or None,
            sensor_path=settings.get("sensor_path") or None,
            device_path=settings.get("device_path") or None,
        )
        adapter = CelloLogicSynthesisAdapter(wrapper=wrapper)
        cello_status = adapter.available().to_dict()
    except Exception as e:
        cello_status = {
            "status": "error",
            "version": None,
            "adapter_name": "cello_wrapper",
            "fallback_used": True,
            "warnings": [{"category": "ERROR", "message": str(e)}]
        }

    response = templates.TemplateResponse(
        request=request,
        name=name,
        status_code=response_status_code,
        context={
            "active_path": request.url.path,
            "lang": lang,
            "_t": _t,
            "_tr": _tr,
            "_status_label": lambda value: _localized_label(value, status_labels),
            "_job_kind_label": lambda value: _localized_label(value, job_kind_labels),
            "_enum_label": lambda value: _localized_label(value, enum_labels),
            "_candidate_limit_label": _candidate_limit_label,
            "_domain_text": _domain_text,
            "unread_notifications": unread_notifications,
            "running_jobs": running_jobs,
            "settings": settings,
            "cello_status": cello_status,
            **context
        },
    )
    response.set_cookie("lang", lang, max_age=30*24*3600)
    return response


def _run_monitor_payload(
    run_id: str,
    services: ApplicationServices,
    *,
    after_event_id: int = 0,
) -> dict[str, object]:
    status = services.runs.status(run_id)
    events = services.runs.events(
        run_id,
        after_event_id=after_event_id,
        limit=100,
    ).get("events", [])
    terminal = status.get("status") in TERMINAL_RUN_STATUSES
    result = services.runs.result(run_id) if terminal else None
    return {
        "run": status,
        "events": events,
        "result": result,
        "monitor": _run_monitor_view(status, events, result),
        "terminal": terminal,
        "next_poll_ms": None if terminal else 3000,
    }


def _run_monitor_view(
    status: dict[str, Any],
    events: list[dict[str, Any]],
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    latest_event = events[-1] if events else None
    return {
        "status_class": _status_class(str(status.get("status") or "")),
        "stage_class": _stage_class(str(status.get("stage") or "")),
        "latest_message": (
            latest_event.get("message")
            if isinstance(latest_event, dict)
            else status.get("error")
            or "No events have been recorded yet."
        ),
        "event_count": len(events),
        "events": [
            {
                **event,
                "stage_class": _stage_class(str(event.get("stage") or "")),
                "status_class": _status_class(str(event.get("status") or "")),
            }
            for event in events
        ],
        "score_summary": _score_summary(result),
        "score_breakdown": _score_breakdown(result),
        "topology_graph": _topology_graph(result),
        "ode_trace": _ode_trace_view(result),
        "search_tree": _search_tree_view(result),
    }


def _status_class(value: str) -> str:
    normalized = value.lower().replace(" ", "_")
    if normalized in {"completed", "pass", "ready"}:
        return "status-ready"
    if normalized in {"error", "failed", "cancelled", "dead_end"}:
        return "status-error"
    if normalized in {"needs_human_input", "warning", "review_required"}:
        return "status-warning"
    if normalized in {"running", "queued", "pending", "evaluated"}:
        return "status-active"
    return "status-neutral"


def _stage_class(value: str) -> str:
    normalized = value.lower()
    if "builder" in normalized or "translator" in normalized:
        return "stage-build"
    if "critic" in normalized or "evaluation" in normalized:
        return "stage-evaluate"
    if "consolidator" in normalized or "complete" in normalized:
        return "stage-complete"
    if "human" in normalized or "pause" in normalized:
        return "stage-human"
    return "stage-neutral"


def _score_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    source = _best_score_source(result)
    score = _score_like(
        result.get("score")
        or result.get("weighted_total_score")
        or source.get("score")
        or source.get("weighted_total_score")
    )
    return {
        "score": score,
        "grade": result.get("grade") or source.get("grade"),
        "mapping_status": source.get("mapping_status") or result.get("mapping_status"),
        "ode_status": source.get("ode_status") or result.get("ode_status"),
    }


def _score_breakdown(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    evaluation = result.get("evaluation")
    if isinstance(evaluation, dict) and isinstance(evaluation.get("dimension_scores"), dict):
        return [
            _score_item(key, value)
            for key, value in evaluation["dimension_scores"].items()
            if _score_like(value) is not None
        ]
    if isinstance(result.get("dimension_scores"), dict):
        return [
            _score_item(key, value)
            for key, value in result["dimension_scores"].items()
            if _score_like(value) is not None
        ]
    source = _best_score_source(result)
    items = []
    score_fields = [
        ("semantic_faithfulness_score", "Semantic faithfulness"),
        ("metabolic_burden_score", "Metabolic burden"),
        ("kinetic_score", "Kinetic"),
        ("robustness_score", "Robustness"),
        ("orthogonality_score", "Orthogonality"),
        ("cello_assignment_score", "Cello assignment"),
        ("toxicity_score", "Toxicity"),
    ]
    benchmark = source.get("benchmark_report")
    if not isinstance(benchmark, dict):
        benchmark = {}
    for key, label in score_fields:
        value = source.get(key, benchmark.get(key, result.get(key)))
        score = _score_like(value)
        if score is not None:
            items.append({"key": key, "label": label, "score": score, "percent": round(score * 100)})
    return items


def _best_score_source(result: dict[str, Any]) -> dict[str, Any]:
    direct = result.get("best_topology")
    if isinstance(direct, dict):
        return direct
    summary = result.get("summary")
    if isinstance(summary, dict) and isinstance(summary.get("best_topology"), dict):
        return summary["best_topology"]
    return result


def _topology_graph(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"nodes": [], "edges": [], "message": "Topology data is not available yet."}
    source = _best_score_source(result)
    graph = source.get("topology_graph") or source.get("graph")
    if isinstance(graph, dict):
        nodes = _normalize_graph_nodes(graph.get("nodes"))
        edges = _normalize_graph_edges(graph.get("edges"))
        if nodes:
            return {"nodes": nodes, "edges": edges, "message": ""}

    nodes = _normalize_graph_nodes(source.get("nodes"))
    edges = _normalize_graph_edges(source.get("edges"))
    if nodes:
        return {"nodes": nodes, "edges": edges, "message": ""}

    interactions = source.get("interactions")
    if isinstance(interactions, list):
        nodes, edges = _graph_from_interactions(interactions)
        if nodes:
            return {"nodes": nodes, "edges": edges, "message": ""}

    verilog = str(source.get("verilog") or result.get("verilog") or "")
    if verilog.strip():
        nodes, edges = _graph_from_verilog(verilog)
        if nodes:
            return {"nodes": nodes, "edges": edges, "message": ""}
    return {"nodes": [], "edges": [], "message": "No topology graph could be inferred."}


def _normalize_graph_nodes(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    nodes = []
    for index, item in enumerate(value):
        if isinstance(item, dict):
            node_id = str(item.get("id") or item.get("node_id") or item.get("name") or f"node_{index}")
            label = str(item.get("label") or item.get("name") or node_id)
            node_type = str(item.get("type") or item.get("part_type") or "signal")
        else:
            node_id = str(item)
            label = node_id
            node_type = "signal"
        if node_id.strip():
            nodes.append({"id": node_id, "label": label, "type": node_type})
    return nodes


def _normalize_graph_edges(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    edges = []
    for item in value:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or item.get("from") or "").strip()
        target = str(item.get("target") or item.get("to") or "").strip()
        if source and target:
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "label": str(item.get("label") or item.get("type") or ""),
                }
            )
    return edges


def _graph_from_interactions(
    interactions: list[Any],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    node_ids: set[str] = set()
    edges = []
    for item in interactions:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        if not source or not target:
            continue
        node_ids.update({source, target})
        edges.append(
            {
                "source": source,
                "target": target,
                "label": str(item.get("interaction_type") or item.get("label") or ""),
            }
        )
    nodes = [{"id": node_id, "label": node_id, "type": "part"} for node_id in sorted(node_ids)]
    return nodes, edges


def _graph_from_verilog(verilog: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    code = _strip_verilog_comments(verilog)
    assignments = re.findall(
        r"\bassign\s+([A-Za-z_]\w*)\s*=\s*([^;]+);",
        code,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not assignments:
        return [], []
    nodes_by_id: dict[str, dict[str, str]] = {}
    edges: list[dict[str, str]] = []
    for index, (target, expression) in enumerate(assignments, start=1):
        logic_id = f"logic_{index}"
        nodes_by_id[logic_id] = {
            "id": logic_id,
            "label": _logic_label(expression),
            "type": "logic",
        }
        nodes_by_id[target] = {"id": target, "label": target, "type": "output"}
        edges.append({"source": logic_id, "target": target, "label": "drives"})
        for token in _expression_tokens(expression):
            nodes_by_id.setdefault(token, {"id": token, "label": token, "type": "input"})
            edges.append({"source": token, "target": logic_id, "label": ""})
    return list(nodes_by_id.values()), edges


def _strip_verilog_comments(value: str) -> str:
    without_block = re.sub(r"/\*.*?\*/", "", value, flags=re.DOTALL)
    return re.sub(r"//.*", "", without_block)


def _expression_tokens(expression: str) -> list[str]:
    reserved = {"assign", "and", "or", "not", "module", "endmodule", "input", "output", "wire"}
    tokens = re.findall(r"\b[A-Za-z_]\w*\b", expression)
    return list(dict.fromkeys(token for token in tokens if token.lower() not in reserved))


def _logic_label(expression: str) -> str:
    compact = " ".join(expression.strip().split())
    if not compact:
        return "logic"
    if len(compact) > 34:
        return f"{compact[:31]}..."
    return compact


def _ode_trace_view(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"status": "unavailable", "series": [], "metrics": [], "message": "ODE trace is not available yet."}
    source = _best_score_source(result)
    trace = source.get("ode_trace")
    if not isinstance(trace, dict):
        trace = result.get("ode_trace")
    if not _valid_ode_trace(trace):
        return {
            "status": str(source.get("ode_status") or result.get("ode_status") or "unavailable"),
            "series": [],
            "metrics": _ode_metrics(source),
            "message": "No saved ODE time series was found for this run.",
        }
    return {
        "status": str(source.get("ode_status") or "simulated"),
        "series": _ode_series(trace),
        "metrics": _ode_metrics(source),
        "message": "",
    }


def _ode_series(trace: dict[str, Any]) -> list[dict[str, Any]]:
    rows = project_ode_trace_rows(trace)
    if not rows:
        return []
    time_values = [row["time"] for row in rows]
    series = []
    for key, label in [
        ("output_protein", "Output protein"),
        ("total_mrna", "Total mRNA"),
        ("total_protein", "Total protein"),
        ("rnap_occupancy", "RNAP occupancy"),
        ("ribosome_occupancy", "Ribosome occupancy"),
    ]:
        values = [row[key] for row in rows if key in row]
        if len(values) != len(rows):
            continue
        points = _sparkline_points(time_values, values)
        if points:
            series.append({"key": key, "label": label, "points": points})
    return series


def _sparkline_points(
    time_values: list[float], values: list[float]
) -> str:
    pairs = list(zip(time_values, values))
    if not pairs:
        return ""
    min_x = min(x for x, _ in pairs)
    max_x = max(x for x, _ in pairs)
    min_y = min(y for _, y in pairs)
    max_y = max(y for _, y in pairs)
    x_span = max(max_x - min_x, 1e-9)
    y_span = max(max_y - min_y, 1e-9)
    points = []
    for x_value, y_value in pairs:
        x_pos = ((x_value - min_x) / x_span) * 100
        y_pos = 36 - ((y_value - min_y) / y_span) * 30 - 3
        points.append(f"{x_pos:.2f},{y_pos:.2f}")
    return " ".join(points)


def _ode_metrics(source: dict[str, Any]) -> list[dict[str, str]]:
    items = []
    for key, label in [
        ("dynamic_margin", "Dynamic margin"),
        ("signal_to_noise_ratio", "SNR"),
        ("metrics_cv", "Output CV"),
        ("monte_carlo_runs", "MC runs"),
    ]:
        value = source.get(key)
        if value is not None:
            items.append({"label": label, "value": _format_monitor_value(value)})
    return items


def _search_tree_view(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"nodes": [], "branches": [], "path": [], "message": "Search history is not available yet."}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    nodes = _normalize_search_nodes(summary.get("tree_summary") or result.get("tree_summary"))
    if not nodes:
        nodes = _normalize_search_nodes(result.get("search_tree"))
    if not nodes:
        return {"nodes": [], "branches": [], "path": [], "message": "No search tree was recorded for this run."}
    node_by_id = {node["node_id"]: node for node in nodes}
    branches = [
        {
            "source": node["parent_id"],
            "target": node["node_id"],
            "label": node["search_mode"],
        }
        for node in nodes
        if node.get("parent_id") in node_by_id
    ]
    current_id = str(summary.get("current_node_id") or result.get("current_node_id") or nodes[-1]["node_id"])
    path = _search_path(nodes, current_id)
    return {"nodes": nodes, "branches": branches, "path": path, "message": ""}


def _normalize_search_nodes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    nodes = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("node_id") or item.get("id") or f"node_{index}").strip()
        if not node_id:
            continue
        score = _score_like(item.get("score"))
        nodes.append(
            {
                "node_id": node_id,
                "parent_id": str(item.get("parent_id") or "").strip(),
                "search_mode": str(item.get("search_mode") or item.get("mode") or "Exploration"),
                "status": str(item.get("status") or "unknown"),
                "score": score,
                "score_label": "-" if score is None else f"{score:.3f}",
                "error_type": str(item.get("error_type") or "NONE"),
                "critic_feedback": str(item.get("critic_feedback") or item.get("feedback") or ""),
            }
        )
    return nodes


def _search_path(nodes: list[dict[str, Any]], current_id: str) -> list[dict[str, Any]]:
    node_by_id = {node["node_id"]: node for node in nodes}
    path = []
    seen: set[str] = set()
    selected = current_id if current_id in node_by_id else nodes[-1]["node_id"]
    while selected and selected in node_by_id and selected not in seen:
        seen.add(selected)
        node = node_by_id[selected]
        path.append(node)
        selected = str(node.get("parent_id") or "")
    return list(reversed(path))


def _format_monitor_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _score_item(key: str, value: Any) -> dict[str, Any]:
    score = _score_like(value)
    assert score is not None
    return {
        "key": key,
        "label": str(key).replace("_", " ").title(),
        "score": score,
        "percent": round(score * 100),
    }


def _score_like(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score < 0:
        return None
    if score > 1 and score <= 100:
        score = score / 100
    return max(0.0, min(1.0, score))


def _assembly_template(
    request: Request,
    services: ApplicationServices,
    name: str,
    *,
    deliverable: dict[str, object] | None = None,
    selected_backbone: str = "",
) -> HTMLResponse:
    return _template(
        request,
        name,
        designs=services.designs.list_v2(),
        backbones=[item.to_dict() for item in services.backbones.list()],
        deliverable=deliverable,
        selected_backbone=selected_backbone,
    )


def _comma_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


_SENSITIVE_KEYS = re.compile(r"api_key|token|password|secret|key", re.IGNORECASE)


def _sanitize_shareable(value: Any) -> Any:
    if isinstance(value, str):
        # 1. Mask paths
        if re.search(r'[a-zA-Z]:\\', value):
            return "[local_path]"

        # 2. Mask secrets / tokens
        masked = re.sub(
            r'(token|api_key|password|secret|key)\s*[:=]\s*[^\s&"\'}]+',
            r'\1=[hidden]',
            value,
            flags=re.IGNORECASE
        )
        return masked

    if isinstance(value, dict):
        return {
            key: "[hidden]" if _SENSITIVE_KEYS.search(str(key)) else _sanitize_shareable(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_shareable(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_shareable(item) for item in value)
    return value


@router.get("/web/designs/{design_id}/exports/project_package")
def download_project_package(
    design_id: str,
    rev: int | None = None,
    services: ApplicationServices = Depends(get_services),
) -> Response:
    import io
    import zipfile
    import json
    import hashlib
    from datetime import datetime, timezone

    # 1. Fetch design revision
    if rev is not None:
        design_v2 = services.designs.get_revision(design_id, rev)
    else:
        design_v2 = services.designs.get_v2(design_id)
    if design_v2 is None:
        raise HTTPException(status_code=404, detail="Design not found.")

    from utils.safety_checker import check_design_export_safety, log_safety_event

    safety_result = check_design_export_safety(design_v2.to_dict(), "project_package")
    if safety_result.status != "safe":
        log_safety_event(
            design_id,
            design_v2.specification.user_intent or design_v2.name,
            safety_result.status,
            safety_result.warnings,
            action="export:project_package",
            categories=safety_result.categories,
            metadata={"revision_number": design_v2.revision.revision_number},
        )
    if not safety_result.export_allowed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "EXPORT_BLOCKED_SAFETY_REVIEW",
                "message": safety_result.redirection_message
                or "Explicit human safety review is required before export.",
                "warnings": safety_result.warnings,
            },
        )

    from application.services import design_ir_from_dict, design_ir_v2_to_v1_payload
    design = design_ir_from_dict(design_ir_v2_to_v1_payload(design_v2.to_dict()))

    # 2. Package Zip
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        # File 1: design.json
        design_json = json.dumps(design_v2.to_dict(), indent=2, ensure_ascii=False)
        zip_file.writestr("design.json", design_json)

        # File 2: circuit.v
        verilog_content = design_v2.extensions.get("verilog", "")
        if verilog_content:
            zip_file.writestr(f"{design.design_id}.v", verilog_content)

        # File 3: parts_bom.csv
        from exporters.bom_exporter import export_bom_csv
        bom_result = export_bom_csv(design)
        if bom_result.ok:
            zip_file.writestr("parts_bom.csv", bom_result.content)

        # File 4: sequences.gb
        from exporters.genbank_exporter import export_genbank
        gb_result = export_genbank(design)
        if gb_result.ok:
            zip_file.writestr("sequences.gb", gb_result.content)

        # File 5: design_sbol3.ttl
        from exporters.sbol3_exporter import export_sbol3_turtle
        sbol_result = export_sbol3_turtle(design)
        if sbol_result.ok:
            zip_file.writestr("design_sbol3.ttl", sbol_result.content)

        claim_payload = claim_boundary_payload(
            design_id=design.design_id,
            revision_id=design_v2.revision.revision_id,
            revision_number=design_v2.revision.revision_number,
            formats=["parts_bom.csv", "sequences.gb", "design_sbol3.ttl"],
        )
        claim_markdown = claim_boundary_markdown(claim_payload)
        claim_json = claim_boundary_json(claim_payload)
        zip_file.writestr("CLAIM_BOUNDARY.md", claim_markdown)
        zip_file.writestr("CLAIM_BOUNDARY.json", claim_json)

        # File 6: manifest.json
        manifest = {
            "project_id": design.design_id,
            "name": design.name,
            "revision_id": design_v2.revision.revision_id,
            "revision_number": design_v2.revision.revision_number,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "schema_version": "2.0",
            "provenance": _sanitize_shareable([
                {
                    "id": p.id,
                    "source_type": p.source_type,
                    "source_uri": p.source_uri,
                    "generated_at": p.generated_at,
                    "metadata": p.metadata,
                }
                for p in design_v2.provenance
            ]),
            "files": [
                {"filename": "design.json", "sha256": hashlib.sha256(design_json.encode('utf-8')).hexdigest()},
            ]
        }
        if verilog_content:
            manifest["files"].append({"filename": f"{design.design_id}.v", "sha256": hashlib.sha256(verilog_content.encode('utf-8')).hexdigest()})
        if bom_result.ok:
            manifest["files"].append({"filename": "parts_bom.csv", "sha256": hashlib.sha256(bom_result.content.encode('utf-8')).hexdigest()})
        if gb_result.ok:
            manifest["files"].append({"filename": "sequences.gb", "sha256": hashlib.sha256(gb_result.content.encode('utf-8')).hexdigest()})
        if sbol_result.ok:
            manifest["files"].append({"filename": "design_sbol3.ttl", "sha256": hashlib.sha256(sbol_result.content.encode('utf-8')).hexdigest()})
        manifest["files"].append({"filename": "CLAIM_BOUNDARY.md", "sha256": hashlib.sha256(claim_markdown.encode('utf-8')).hexdigest()})
        manifest["files"].append({"filename": "CLAIM_BOUNDARY.json", "sha256": hashlib.sha256(claim_json.encode('utf-8')).hexdigest()})

        zip_file.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

    zip_buffer.seek(0)

    headers = {
        "Content-Disposition": f'attachment; filename="{design.design_id}_project_package.zip"',
        "X-Safety-Status": safety_result.status,
        "X-Safety-Warning-Count": str(len(safety_result.warnings)),
        "X-Safety-Review-Required": str(
            safety_result.requires_human_review
        ).lower(),
    }
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers=headers,
    )


@router.get("/web/designs/{design_id}/share_summary", response_class=HTMLResponse)
def share_summary(
    design_id: str,
    request: Request,
    rev: int | None = None,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    from copy import deepcopy
    # 1. Fetch design revision
    if rev is not None:
        design_v2 = services.designs.get_revision(design_id, rev)
    else:
        design_v2 = services.designs.get_v2(design_id)
    if design_v2 is None:
        raise HTTPException(status_code=404, detail="Design not found.")

    from application.services import design_ir_from_dict, design_ir_v2_to_v1_payload
    design = design_ir_from_dict(design_ir_v2_to_v1_payload(design_v2.to_dict()))

    # Apply recursive masking before any design field reaches the share template.
    sanitized_payload = _sanitize_shareable(deepcopy(design_v2).to_dict())
    from schemas.design_ir_v2 import design_ir_v2_from_dict
    design_v2_masked = design_ir_v2_from_dict(sanitized_payload)
    design = design_ir_from_dict(
        design_ir_v2_to_v1_payload(design_v2_masked.to_dict())
    )

    from web.design_views import build_design_context_view
    view = build_design_context_view(design_id, rev, services)

    return _template(
        request,
        "share_summary.html",
        design=design,
        design_v2=design_v2_masked,
        view=view,
        readiness=view.readiness,
    )


# ==============================================================================
# Settings, Candidates, Decision History, and Simulation routes
# ==============================================================================

def _with_cello_configuration_status(
    settings: dict[str, Any], cello_status: dict[str, Any]
) -> dict[str, Any]:
    paths = {
        "ucf": str(settings.get("ucf_path") or "").strip(),
        "sensor": str(settings.get("sensor_path") or "").strip(),
        "device": str(settings.get("device_path") or "").strip(),
    }
    cello_status["configuration"] = {
        "command_configured": bool(str(settings.get("cello_command") or "").strip()),
        **{
            f"{name}_configured": bool(path)
            for name, path in paths.items()
        },
        **{
            f"{name}_exists": bool(path) and Path(path).is_file()
            for name, path in paths.items()
        },
    }
    return cello_status

@router.get("/web/settings", response_class=HTMLResponse)
def web_settings_page(
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    from tools.cello_wrapper import CelloWrapper
    from tools.tool_adapters import CelloLogicSynthesisAdapter

    settings = services.settings.get_settings_masked()

    cello_cmd = settings.get("cello_command") or None
    wrapper = CelloWrapper(
        cello_command=cello_cmd,
        cello_artifact_format=settings.get("cello_artifact_format") or "cello_v2",
        ucf_path=settings.get("ucf_path") or None,
        sensor_path=settings.get("sensor_path") or None,
        device_path=settings.get("device_path") or None,
    )
    adapter = CelloLogicSynthesisAdapter(wrapper=wrapper)
    try:
        cello_status = adapter.available().to_dict()
    except Exception as e:
        cello_status = {
            "status": "error",
            "version": None,
            "adapter_name": "cello_wrapper",
            "fallback_used": True,
            "warnings": [{"category": "ERROR", "message": str(e)}]
        }
    cello_status = _with_cello_configuration_status(settings, cello_status)

    return _template(
        request,
        "settings.html",
        settings=settings,
        cello_status=cello_status,
        credentials_required=(
            request.query_params.get("credentials_required") == "1"
        ),
    )


@router.post("/web/settings", response_class=HTMLResponse)
def web_save_settings(
    request: Request,
    provider: Annotated[str, Form()],
    model_name: Annotated[str, Form()],
    api_key: Annotated[str, Form()] = "",
    api_base: Annotated[str, Form()] = "",
    cello_command: Annotated[str, Form()] = "",
    cello_artifact_format: Annotated[
        str, Form(pattern="^(cello_v2|cello21)$")
    ] = "cello_v2",
    ucf_path: Annotated[str, Form()] = "",
    sensor_path: Annotated[str, Form()] = "",
    device_path: Annotated[str, Form()] = "",
    default_host: Annotated[str, Form()] = "Escherichia coli",
    default_compute_budget: Annotated[int, Form()] = 6,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    from tools.cello_wrapper import CelloWrapper
    from tools.tool_adapters import CelloLogicSynthesisAdapter

    payload = {
        "provider": provider.strip(),
        "model_name": model_name.strip(),
        "api_key": api_key.strip(),
        "api_base": api_base.strip(),
        "cello_command": cello_command.strip(),
        "cello_artifact_format": cello_artifact_format.strip(),
        "ucf_path": ucf_path.strip(),
        "sensor_path": sensor_path.strip(),
        "device_path": device_path.strip(),
        "default_host": default_host.strip(),
        "default_compute_budget": default_compute_budget,
    }
    services.settings.save_settings(payload)
    settings = services.settings.get_settings_masked()

    cello_cmd = settings.get("cello_command") or None
    wrapper = CelloWrapper(
        cello_command=cello_cmd,
        cello_artifact_format=settings.get("cello_artifact_format") or "cello_v2",
        ucf_path=settings.get("ucf_path") or None,
        sensor_path=settings.get("sensor_path") or None,
        device_path=settings.get("device_path") or None,
    )
    adapter = CelloLogicSynthesisAdapter(wrapper=wrapper)
    try:
        cello_status = adapter.available().to_dict()
    except Exception as e:
        cello_status = {
            "status": "error",
            "version": None,
            "adapter_name": "cello_wrapper",
            "fallback_used": True,
            "warnings": [{"category": "ERROR", "message": str(e)}]
        }
    cello_status = _with_cello_configuration_status(settings, cello_status)

    return _template(
        request,
        "settings.html",
        settings=settings,
        cello_status=cello_status,
        success_msg=_localized(request, "設定儲存成功！", "Settings saved successfully."),
    )


@router.post("/web/settings/api-key/delete", response_class=HTMLResponse)
def web_clear_settings_api_key(
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    from tools.cello_wrapper import CelloWrapper
    from tools.tool_adapters import CelloLogicSynthesisAdapter

    services.settings.clear_api_key()
    settings = services.settings.get_settings_masked()

    cello_cmd = settings.get("cello_command") or None
    wrapper = CelloWrapper(
        cello_command=cello_cmd,
        cello_artifact_format=settings.get("cello_artifact_format") or "cello_v2",
        ucf_path=settings.get("ucf_path") or None,
        sensor_path=settings.get("sensor_path") or None,
        device_path=settings.get("device_path") or None,
    )
    adapter = CelloLogicSynthesisAdapter(wrapper=wrapper)
    try:
        cello_status = adapter.available().to_dict()
    except Exception as e:
        cello_status = {
            "status": "error",
            "version": None,
            "adapter_name": "cello_wrapper",
            "fallback_used": True,
            "warnings": [{"category": "ERROR", "message": str(e)}]
        }
    cello_status = _with_cello_configuration_status(settings, cello_status)

    return _template(
        request,
        "settings.html",
        settings=settings,
        cello_status=cello_status,
        success_msg=_localized(request, "金鑰已清除！", "API key cleared."),
    )


@router.get("/web/runs/{run_id}/candidates", response_class=HTMLResponse)
def web_candidates_list(
    run_id: str,
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    try:
        run_status = services.runs.status(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run_status.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Run not found.")

    try:
        run_result = services.runs.result(run_id)
    except Exception:
        run_result = None

    from web.candidate_views import build_candidate_list_view
    view = build_candidate_list_view(run_id, run_status, run_result)

    return _template(
        request,
        "candidates.html",
        view=view,
    )


@router.get("/web/runs/{run_id}/candidates/compare", response_class=HTMLResponse)
def web_candidate_compare(
    run_id: str,
    indexes: str,
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    try:
        run_status = services.runs.status(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run_status.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Run not found.")

    try:
        run_result = services.runs.result(run_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Run result not available.")

    try:
        idx_list = [int(i.strip()) for i in indexes.split(",") if i.strip()]
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid indexes parameter format. Expected comma-separated integers.")

    if not idx_list:
        raise HTTPException(status_code=400, detail="Invalid indexes parameter format. No indexes provided.")

    from web.candidate_views import build_candidate_comparison_view
    try:
        view = build_candidate_comparison_view(run_id, run_status, run_result, idx_list)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return _template(
        request,
        "candidate_compare.html",
        view=view,
    )


@router.get("/web/runs/{run_id}/candidates/{candidate_index}", response_class=HTMLResponse)
def web_candidate_detail(
    run_id: str,
    candidate_index: int,
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    try:
        run_status = services.runs.status(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run_status.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Run not found.")

    status_str = run_status.get("status", "unknown")
    if status_str not in TERMINAL_RUN_STATUSES:
        raise HTTPException(status_code=400, detail="Run is not completed yet.")

    try:
        run_result = services.runs.result(run_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Run result not available or unparseable.")

    from web.candidate_views import build_candidate_detail_view
    try:
        view = build_candidate_detail_view(run_id, run_status, run_result, candidate_index)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return _template(
        request,
        "candidate_detail.html",
        view=view,
    )


def _extract_inputs_from_verilog(verilog: str) -> list[str]:
    clean_verilog = re.sub(r"//.*", "", verilog)
    clean_verilog = re.sub(r"/\*.*?\*/", "", clean_verilog, flags=re.DOTALL)
    inputs = []
    for m in re.findall(r"\binput\b\s+([^;)]+)", clean_verilog):
        parts = m.split(",")
        for p in parts:
            name = p.strip()
            if name.startswith("input "):
                name = name[6:].strip()
            if "output" in name:
                name = name.split("output")[0].strip()
            if name and name not in ["output", "module", "wire", "reg", "assign"]:
                name = re.sub(r"[^a-zA-Z0-9_]", "", name)
                if name and name not in inputs:
                    inputs.append(name)
    if not inputs:
        inputs = ["A"]
    return inputs


@router.get("/web/runs/{run_id}/candidates/{candidate_index}/simulate", response_class=HTMLResponse)
def web_candidate_simulate_get(
    run_id: str,
    candidate_index: int,
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    try:
        run_status = services.runs.status(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run_status.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Run not found.")

    try:
        run_result = services.runs.result(run_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Run result not available.")

    from web.candidate_views import build_candidate_detail_view
    try:
        view = build_candidate_detail_view(run_id, run_status, run_result, candidate_index)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    inputs = _extract_inputs_from_verilog(view.verilog_code)

    form_data = {
        "simulation_time": 300,
        "sample_count": 50,
        "noise_fraction": 0.1,
        "random_seed": "",
    }

    return _template(
        request,
        "candidate_simulation.html",
        view=view,
        inputs=inputs,
        form_data=form_data,
        results=None,
    )


@router.post("/web/runs/{run_id}/candidates/{candidate_index}/simulate", response_class=HTMLResponse)
async def web_candidate_simulate_post(
    run_id: str,
    candidate_index: int,
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    try:
        run_status = services.runs.status(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run_status.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Run not found.")

    try:
        run_result = services.runs.result(run_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Run result not available.")

    from web.candidate_views import build_candidate_detail_view
    try:
        view = build_candidate_detail_view(run_id, run_status, run_result, candidate_index)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    form = await request.form()
    simulation_time = float(form.get("simulation_time", 300.0))
    sample_count = int(form.get("sample_count", 50))
    noise_fraction = float(form.get("noise_fraction", 0.1))
    random_seed_str = form.get("random_seed", "")
    random_seed = int(random_seed_str) if random_seed_str.strip() else None

    inputs = _extract_inputs_from_verilog(view.verilog_code)

    input_specs = {}
    for sig in inputs:
        input_type = form.get(f"input_type_{sig}", "constant")
        if input_type == "constant":
            input_specs[sig] = {
                "type": "constant",
                "value": float(form.get(f"input_value_{sig}", 1.0)),
            }
        elif input_type == "step":
            input_specs[sig] = {
                "type": "step",
                "step_start": float(form.get(f"input_step_start_{sig}", 0.0)),
                "step_end": float(form.get(f"input_step_end_{sig}", 1.0)),
                "step_time": float(form.get(f"input_step_time_{sig}", 150.0)),
            }
        elif input_type == "pulse":
            input_specs[sig] = {
                "type": "pulse",
                "pulse_basal": float(form.get(f"input_pulse_basal_{sig}", 0.0)),
                "pulse_active": float(form.get(f"input_pulse_active_{sig}", 1.0)),
                "pulse_start": float(form.get(f"input_pulse_start_{sig}", 100.0)),
                "pulse_end": float(form.get(f"input_pulse_end_{sig}", 300.0)),
            }

    from web.candidate_views import _extract_candidate_topologies
    refs = _extract_candidate_topologies(run_id, run_result)
    ref = refs[candidate_index]

    payload = {
        "simulation_time": simulation_time,
        "sample_count": sample_count,
        "noise_fraction": noise_fraction,
        "random_seed": random_seed,
        "input_specs": input_specs,
        "candidate": ref.topology,
    }

    sim_result = services.simulations.simulate(payload)

    form_data = {
        "simulation_time": simulation_time,
        "sample_count": sample_count,
        "noise_fraction": noise_fraction,
        "random_seed": random_seed_str,
    }
    for sig in inputs:
        form_data[f"input_type_{sig}"] = form.get(f"input_type_{sig}", "constant")
        form_data[f"input_value_{sig}"] = form.get(f"input_value_{sig}", "1.0")
        form_data[f"input_step_start_{sig}"] = form.get(f"input_step_start_{sig}", "0.0")
        form_data[f"input_step_end_{sig}"] = form.get(f"input_step_end_{sig}", "1.0")
        form_data[f"input_step_time_{sig}"] = form.get(f"input_step_time_{sig}", "150.0")
        form_data[f"input_pulse_basal_{sig}"] = form.get(f"input_pulse_basal_{sig}", "0.0")
        form_data[f"input_pulse_active_{sig}"] = form.get(f"input_pulse_active_{sig}", "1.0")
        form_data[f"input_pulse_start_{sig}"] = form.get(f"input_pulse_start_{sig}", "100.0")
        form_data[f"input_pulse_end_{sig}"] = form.get(f"input_pulse_end_{sig}", "300.0")

    results = dict(sim_result["candidate"]) if sim_result else None
    if results:
        import json
        results["raw_json"] = json.dumps(sim_result, indent=2, ensure_ascii=False)

    return _template(
        request,
        "candidate_simulation.html",
        view=view,
        inputs=inputs,
        form_data=form_data,
        results=results,
    )




@router.post("/web/runs/{run_id}/candidates/{candidate_index}/promote")
def web_candidate_promote(
    run_id: str,
    candidate_index: int,
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    try:
        run_status = services.runs.status(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run_status.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Run not found.")

    try:
        run_result = services.runs.result(run_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Run result not available.")

    from web.candidate_views import _extract_candidate_topologies
    refs = _extract_candidate_topologies(run_id, run_result)
    if candidate_index < 0 or candidate_index >= len(refs):
        raise HTTPException(status_code=404, detail="Candidate index is out of range.")

    ref = refs[candidate_index]
    host_organism = run_status.get("summary", {}).get("host_organism") or run_status.get("request", {}).get("host_organism") or "Escherichia coli"

    from uuid import uuid4
    design_id = f"design_{uuid4().hex[:12]}"

    from schemas.design_ir import topology_to_design_ir
    design_ir = topology_to_design_ir(ref.topology, host_organism=host_organism, design_id=design_id)
    design_ir.name = f"Design from Run {run_id[:8]} Candidate #{candidate_index + 1}"

    services.designs.save(design_ir)
    return RedirectResponse(f"/web/designs/{design_ir.design_id}", status_code=303)


@router.get("/web/runs/{run_id}/decision-history", response_class=HTMLResponse)
def web_run_decision_history(
    run_id: str,
    request: Request,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    try:
        status = services.runs.status(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found.")
    if status.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Run not found.")
    events = services.runs.events(run_id, limit=100).get("events", [])
    try:
        result = services.runs.result(run_id)
    except Exception:
        result = None
    monitor = _run_monitor_view(status, events, result)
    return _template(
        request,
        "run_decision_history.html",
        run=status,
        events=events,
        result=result,
        monitor=monitor,
    )


@router.post("/web/runs/{run_id}/cancel")
def web_run_cancel(
    run_id: str,
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    try:
        services.runs.cancel(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found.")
    return RedirectResponse(f"/web/runs/{run_id}", status_code=303)


@router.post("/web/runs/{run_id}/retry")
def web_run_retry(
    run_id: str,
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    try:
        status = services.runs.status(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found.")

    req = status.get("request") or {}
    retry_request = {
        "user_intent": (
            req.get("user_intent") or status.get("user_intent") or "Retry run"
        ),
        "host_organism": (
            req.get("host_organism")
            or status.get("host_organism")
            or "Escherichia coli"
        ),
        "compute_budget": (
            req.get("compute_budget") or status.get("compute_budget") or 6
        ),
    }
    for key in (
        "model_name",
        "api_base",
        "enable_rag",
        "enable_ode",
        "enable_skill_extraction",
        "monte_carlo_samples",
        "cello_command",
        "ucf_path",
    ):
        if key in req:
            retry_request[key] = req[key]

    new_run = services.runs.start(retry_request)
    new_run_id = new_run["run_id"]
    return RedirectResponse(f"/web/runs/{new_run_id}", status_code=303)


@router.post("/web/research/runs/{run_id}/cancel")
def web_research_run_cancel(
    run_id: str,
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    try:
        services.research.cancel(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found.")
    return RedirectResponse(f"/web/research/runs/{run_id}", status_code=303)


@router.post("/web/research/runs/{run_id}/retry")
def web_research_run_retry(
    run_id: str,
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    try:
        status = services.research.status(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found.")

    req = status.get("request") or {}
    name = req.get("name") or status.get("name") or "Retry run"
    logic_expression = req.get("logic_expression") or status.get("logic_expression") or ""
    host_organism = req.get("host_organism") or status.get("host_organism") or "Escherichia coli"
    extra_budget = req.get("extra_budget") or status.get("extra_budget") or 5

    new_run = services.research.submit_run(
        name=name,
        logic_expression=logic_expression,
        host_organism=host_organism,
        extra_budget=extra_budget,
    )
    new_run_id = new_run.run_id
    return RedirectResponse(f"/web/research/runs/{new_run_id}", status_code=303)


def _get_design_topology(design_id: str, services: ApplicationServices) -> dict[str, Any]:
    design = services.designs.get_v2(design_id)
    if design is None:
        raise HTTPException(status_code=404, detail="Design not found.")
    spec = services.designs.simulation_spec(design_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Design simulation spec not found.")
    verilog = str(spec.get("verilog") or "")
    if not verilog:
        raise ValueError("The selected design has no Verilog topology.")
    return {
        "verilog": verilog,
        "truth_table": design.specification.truth_table,
        "chassis": spec.get("chassis"),
        "copy_number": spec.get("copy_number"),
        "biokinetic_parameters": spec.get("parameters", {}),
    }


def _handle_simulation_get(
    design_id: str,
    request: Request,
    rev: int | None,
    run_id: str | None,
    template_name: str,
    services: ApplicationServices,
) -> HTMLResponse:
    design = services.designs.get(design_id)
    if design is None:
        raise HTTPException(status_code=404, detail="Design not found.")

    design_v2 = services.designs.get_v2(design_id)
    if design_v2 is None:
        raise HTTPException(status_code=404, detail="Design not found.")

    from web.design_views import build_design_context_view
    try:
        view = build_design_context_view(design_id, rev, services)
    except KeyError:
        raise HTTPException(status_code=404, detail="Design not found.")

    run_data = None
    if run_id:
        try:
            status = services.research.status(run_id)
            try:
                result = services.research.result(run_id)
            except Exception:
                result = None
            run_data = {
                "status": status.get("status"),
                "result": result,
            }
        except KeyError:
            pass

    is_historical = rev is not None
    viewed_rev = rev if rev is not None else view.latest_rev

    extra_context = {}
    if template_name == "simulation_fit.html":
        extra_context["snapshots"] = services.simulations.parameter_fit_repository.list()

    return _template(
        request,
        template_name,
        design=design,
        design_v2=design_v2,
        view=view,
        run_id=run_id,
        run_data=run_data,
        is_historical=is_historical,
        viewed_rev=viewed_rev,
        **extra_context
    )


@router.get("/web/designs/{design_id}/simulation/ode", response_class=HTMLResponse)
def web_simulation_ode_get(
    design_id: str,
    request: Request,
    rev: int | None = None,
    run_id: str | None = None,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    return _handle_simulation_get(design_id, request, rev, run_id, "simulation_ode.html", services)


@router.post("/web/designs/{design_id}/simulation/ode")
async def web_simulation_ode_post(
    design_id: str,
    request: Request,
    rev: int | None = None,
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    form = await request.form()
    simulation_time = float(form.get("simulation_time", 600.0))
    sample_count = int(form.get("sample_count", 80))
    noise_fraction = float(form.get("noise_fraction", 0.15))
    random_seed_str = form.get("random_seed", "")
    random_seed = int(random_seed_str) if random_seed_str.strip() else None

    topology = _get_design_topology(design_id, services)
    inputs = _extract_inputs_from_verilog(topology.get("verilog", ""))

    temporal_inputs = {}
    for sig in inputs:
        input_type = form.get(f"input_type_{sig}", "constant")
        if input_type == "constant":
            temporal_inputs[sig] = {
                "type": "constant",
                "value": float(form.get(f"input_value_{sig}", 1.0)),
            }
        elif input_type == "step":
            temporal_inputs[sig] = {
                "type": "step",
                "step_start": float(form.get(f"input_step_start_{sig}", 0.0)),
                "step_end": float(form.get(f"input_step_end_{sig}", 1.0)),
                "step_time": float(form.get(f"input_step_time_{sig}", 150.0)),
            }
        elif input_type == "pulse":
            temporal_inputs[sig] = {
                "type": "pulse",
                "pulse_start": float(form.get(f"input_pulse_start_{sig}", 100.0)),
                "pulse_end": float(form.get(f"input_pulse_end_{sig}", 300.0)),
                "pulse_active": float(form.get(f"input_pulse_active_{sig}", 1.5)),
                "pulse_basal": float(form.get(f"input_pulse_basal_{sig}", 0.0)),
            }

    payload = {
        "design_id": design_id,
        "topology": topology,
        "simulation_time": simulation_time,
        "sample_count": sample_count,
        "noise_fraction": noise_fraction,
        "random_seed": random_seed,
        "temporal_inputs": temporal_inputs,
    }

    run = services.research.start_simulation(payload)
    run_id = run["run_id"]

    rev_param = f"&rev={rev}" if rev is not None else ""
    return RedirectResponse(
        f"/web/designs/{design_id}/simulation/ode?run_id={run_id}{rev_param}",
        status_code=303
    )


@router.get("/web/designs/{design_id}/simulation/ssa", response_class=HTMLResponse)
def web_simulation_ssa_get(
    design_id: str,
    request: Request,
    rev: int | None = None,
    run_id: str | None = None,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    return _handle_simulation_get(design_id, request, rev, run_id, "simulation_ssa.html", services)


@router.post("/web/designs/{design_id}/simulation/ssa")
async def web_simulation_ssa_post(
    design_id: str,
    request: Request,
    rev: int | None = None,
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    form = await request.form()
    runs = int(form.get("runs", 5))
    scale_factor = float(form.get("scale_factor", 5.0))
    max_steps = int(form.get("max_steps", 1000))

    topology = _get_design_topology(design_id, services)

    payload = {
        "design_id": design_id,
        "topology": topology,
        "runs": runs,
        "scale_factor": scale_factor,
        "max_steps": max_steps,
    }

    run = services.research.start_ssa_simulation(payload)
    run_id = run["run_id"]

    rev_param = f"&rev={rev}" if rev is not None else ""
    return RedirectResponse(
        f"/web/designs/{design_id}/simulation/ssa?run_id={run_id}{rev_param}",
        status_code=303
    )


@router.get("/web/designs/{design_id}/simulation/sweep", response_class=HTMLResponse)
def web_simulation_sweep_get(
    design_id: str,
    request: Request,
    rev: int | None = None,
    run_id: str | None = None,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    return _handle_simulation_get(design_id, request, rev, run_id, "simulation_sweep.html", services)


@router.post("/web/designs/{design_id}/simulation/sweep")
async def web_simulation_sweep_post(
    design_id: str,
    request: Request,
    rev: int | None = None,
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    form = await request.form()
    parameter_name = form.get("parameter_name")
    sweep_values_str = form.get("sweep_values", "")
    sweep_values = [float(v.strip()) for v in sweep_values_str.split(",") if v.strip()]

    topology = _get_design_topology(design_id, services)

    payload = {
        "design_id": design_id,
        "topology": topology,
        "parameter_name": parameter_name,
        "sweep_values": sweep_values,
    }

    run = services.research.start_parameter_sweep(payload)
    run_id = run["run_id"]

    rev_param = f"&rev={rev}" if rev is not None else ""
    return RedirectResponse(
        f"/web/designs/{design_id}/simulation/sweep?run_id={run_id}{rev_param}",
        status_code=303
    )


@router.get("/web/designs/{design_id}/simulation/bifurcation", response_class=HTMLResponse)
def web_simulation_bifurcation_get(
    design_id: str,
    request: Request,
    rev: int | None = None,
    run_id: str | None = None,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    return _handle_simulation_get(design_id, request, rev, run_id, "simulation_bifurcation.html", services)


@router.post("/web/designs/{design_id}/simulation/bifurcation")
async def web_simulation_bifurcation_post(
    design_id: str,
    request: Request,
    rev: int | None = None,
    services: ApplicationServices = Depends(get_services),
) -> RedirectResponse:
    form = await request.form()
    input_name = form.get("input_name")
    input_values_str = form.get("input_values", "")
    input_values = [float(v.strip()) for v in input_values_str.split(",") if v.strip()]

    topology = _get_design_topology(design_id, services)

    payload = {
        "design_id": design_id,
        "topology": topology,
        "input_name": input_name,
        "input_values": input_values,
    }

    run = services.research.start_bifurcation_sweep(payload)
    run_id = run["run_id"]

    rev_param = f"&rev={rev}" if rev is not None else ""
    return RedirectResponse(
        f"/web/designs/{design_id}/simulation/bifurcation?run_id={run_id}{rev_param}",
        status_code=303
    )


@router.get("/web/designs/{design_id}/simulation/fit", response_class=HTMLResponse)
def web_simulation_fit_get(
    design_id: str,
    request: Request,
    rev: int | None = None,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    design = services.designs.get(design_id)
    if design is None:
        raise HTTPException(status_code=404, detail="Design not found.")
    design_v2 = services.designs.get_v2(design_id)
    if design_v2 is None:
        raise HTTPException(status_code=404, detail="Design not found.")

    from web.design_views import build_design_context_view
    try:
        view = build_design_context_view(design_id, rev, services)
    except KeyError:
        raise HTTPException(status_code=404, detail="Design not found.")

    snapshots = services.simulations.parameter_fit_repository.list()
    is_historical = rev is not None
    viewed_rev = rev if rev is not None else view.latest_rev

    return _template(
        request,
        "simulation_fit.html",
        design=design,
        design_v2=design_v2,
        view=view,
        snapshots=snapshots,
        selected_snapshot_id=None,
        comparison_result=None,
        error=None,
        is_historical=is_historical,
        viewed_rev=viewed_rev,
    )


@router.post("/web/designs/{design_id}/simulation/fit", response_class=HTMLResponse)
async def web_simulation_fit_post(
    design_id: str,
    request: Request,
    rev: int | None = None,
    services: ApplicationServices = Depends(get_services),
) -> HTMLResponse:
    design = services.designs.get(design_id)
    if design is None:
        raise HTTPException(status_code=404, detail="Design not found.")
    design_v2 = services.designs.get_v2(design_id)
    if design_v2 is None:
        raise HTTPException(status_code=404, detail="Design not found.")

    from web.design_views import build_design_context_view
    try:
        view = build_design_context_view(design_id, rev, services)
    except KeyError:
        raise HTTPException(status_code=404, detail="Design not found.")

    form = await request.form()
    snapshot_id = form.get("snapshot_id")
    simulation_time = float(form.get("simulation_time", 600.0))
    sample_count = int(form.get("sample_count", 80))

    topology = _get_design_topology(design_id, services)

    comparison_result = None
    error = None
    try:
        comparison_result = services.simulations.compare_default_vs_fitted(
            topology,
            snapshot_id,
            simulation_time=simulation_time,
            sample_count=sample_count,
        )
    except Exception as e:
        error = str(e)

    snapshots = services.simulations.parameter_fit_repository.list()
    is_historical = rev is not None
    viewed_rev = rev if rev is not None else view.latest_rev

    return _template(
        request,
        "simulation_fit.html",
        design=design,
        design_v2=design_v2,
        view=view,
        snapshots=snapshots,
        selected_snapshot_id=snapshot_id,
        comparison_result=comparison_result,
        error=error,
        is_historical=is_historical,
        viewed_rev=viewed_rev,
    )
