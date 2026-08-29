from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_services
from api.main import app
from application.services import create_application_services
from repositories.json_repository import JsonRepository, RepositoryError
from schemas.import_draft import ImportDraft


def _draft_payload(draft_id: str = "external_design_1") -> dict:
    return {
        "draft_id": draft_id,
        "name": f"External design {draft_id}",
        "source_type": "literature",
        "source_uri": "https://doi.org/10.0000/example",
        "citation": "Example et al.",
        "host_organism": "Escherichia coli",
        "inputs": ["A", "B"],
        "outputs": ["GFP"],
        "logic_expression": "GFP = A AND NOT B",
        "validation_status": "experimentally_validated",
        "validation_notes": "Reported in the source publication.",
        "parts": [
            {
                "id": "promoter_a",
                "name": "Promoter A",
                "part_type": "promoter",
                "role": "Input promoter",
                "sequence": "AAAAAA",
                "host_compatibility": ["Escherichia coli"],
                "evidence": {
                    "field_path": "parts.promoter_a",
                    "status": "explicit",
                    "locator": "Figure 1",
                },
            },
            {
                "id": "gfp",
                "name": "GFP",
                "part_type": "CDS",
                "role": "Reporter",
                "sequence": "ATGAAATAA",
                "host_compatibility": ["Escherichia coli"],
                "evidence": {
                    "field_path": "parts.gfp",
                    "status": "explicit",
                    "locator": "Methods",
                },
            },
        ],
        "interactions": [
            {
                "source": "promoter_a",
                "target": "gfp",
                "interaction_type": "expression",
                "label": "Promoter drives GFP",
            }
        ],
        "evidence": [
            {
                "field_path": "design_summary",
                "status": "explicit",
                "locator": "Figure 1",
            }
        ],
        "notes": "",
        "created_at": "2026-06-14T00:00:00+00:00",
        "schema_version": "1.0",
    }


@pytest.fixture
def client(tmp_path: Path):
    services = create_application_services(tmp_path / "api_data")
    app.state.test_services = services
    app.dependency_overrides[get_services] = lambda: services
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_json_repository_writes_and_rejects_path_traversal(tmp_path: Path) -> None:
    repository = JsonRepository(tmp_path / "records")

    repository.save("design_1", {"design_id": "design_1"})

    assert repository.get("design_1") == {"design_id": "design_1"}
    assert not list((tmp_path / "records").glob("*.tmp"))
    with pytest.raises(RepositoryError):
        repository.get("../outside")


def test_import_service_persists_draft_and_confirmed_design(tmp_path: Path) -> None:
    services = create_application_services(tmp_path / "api_data")
    draft = services.imports.import_json(_draft_payload())

    design = services.imports.confirm_by_id(draft.draft_id)

    assert services.imports.get_draft(draft.draft_id) is not None
    assert services.designs.get(design.design_id) is not None
    assert design.validation_status["experimental_validation"] == (
        "experimentally_validated"
    )


def test_health_and_openapi_are_available(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"
    assert client.get("/openapi.json").status_code == 200


def test_validate_import_confirm_and_list_design(client: TestClient) -> None:
    payload = _draft_payload()

    validation = client.post("/api/v1/imports/drafts/validate", json=payload)
    imported = client.post("/api/v1/imports/json", json={"draft": payload})
    confirmed = client.post(
        f"/api/v1/imports/{payload['draft_id']}/confirm"
    )
    listed = client.get("/api/v1/designs")
    fetched = client.get(f"/api/v1/designs/{payload['draft_id']}")

    assert validation.status_code == 200
    assert validation.json()["data"]["can_import"] is True
    assert imported.status_code == 201
    assert confirmed.status_code == 201
    assert confirmed.json()["data"]["design_id"] == payload["draft_id"]
    assert listed.json()["data"]["count"] == 1
    assert fetched.json()["data"]["name"] == payload["name"]


def test_v2_sequence_analysis_and_optimization_evaluation(client: TestClient) -> None:
    payload = _draft_payload("sequence_api_design")
    payload["parts"][1]["sequence"] = "ATGAAATAAGGTCTCTAA"
    client.post("/api/v1/imports/json", json={"draft": payload})
    client.post(f"/api/v1/imports/{payload['draft_id']}/confirm")

    analysis = client.post(
        f"/api/v2/designs/{payload['draft_id']}/sequence-analysis",
        json={"part_ids": ["gfp"]},
    )
    optimization = client.post(
        f"/api/v2/designs/{payload['draft_id']}/sequence-optimization/evaluate",
        json={
            "objective": "sequence_quality_baseline",
            "part_ids": ["gfp"],
        },
    )

    assert analysis.status_code == 200
    data = analysis.json()["data"]
    assert data["status"] == "blocked"
    assert data["summary"]["part_count"] == 1
    assert any(
        issue["code"] == "INTERNAL_BSAI_SITE"
        for issue in data["results"][0]["issues"]
    )
    assert optimization.status_code == 200
    result = optimization.json()["data"]
    assert result["status"] == "needs_review"
    assert result["results"][0]["optimized_sequence"] is None
    assert result["results"][0]["issues"][0]["code"] == "NO_OPTIMIZED_SEQUENCE"


def test_genbank_import_creates_reviewable_incomplete_draft(
    client: TestClient,
) -> None:
    content = """LOCUS       API_TEST                 18 bp    DNA
SOURCE      synthetic construct
  ORGANISM  Escherichia coli
FEATURES             Location/Qualifiers
     promoter        1..6
                     /label="pTest"
     CDS             7..15
                     /gene="gfp"
ORIGIN
        1 aaaaaaatgaaataaacc
//
"""
    response = client.post(
        "/api/v1/imports/genbank",
        json={"filename": "api_test.gb", "content": content},
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["draft"]["parts"][0]["name"] == "pTest"
    assert data["validation"]["can_import"] is False
    assert "inputs" in data["validation"]["missing_fields"]


def test_comparison_evaluation_and_bom_export(client: TestClient) -> None:
    for draft_id in ("design_a", "design_b"):
        payload = _draft_payload(draft_id)
        assert client.post(
            "/api/v1/imports/json",
            json={"draft": payload},
        ).status_code == 201
        assert client.post(
            f"/api/v1/imports/{draft_id}/confirm"
        ).status_code == 201

    comparison = client.post(
        "/api/v1/comparisons",
        json={
            "left_design_id": "design_a",
            "right_design_id": "design_b",
            "left_metrics": {"score": 0.6},
            "right_metrics": {"score": 0.8},
        },
    )
    evaluation = client.post(
        "/api/v1/evaluations",
        json={"candidate": {"verilog": "assign Y = A;", "gate_count": 1}},
    )
    export = client.get("/api/v1/designs/design_a/exports/bom")

    assert comparison.status_code == 200
    assert comparison.json()["data"]["metric_changes"][0]["delta"] == pytest.approx(
        0.2
    )
    assert evaluation.status_code == 200
    assert "weighted_total_score" in evaluation.json()["data"]
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    assert export.headers["x-claim-boundary"] == "computational-exchange-artifact-only"
    assert export.headers["x-not-wet-lab-validation"] == "true"
    assert export.headers["x-not-experimental-protocol"] == "true"
    assert "design_id" in export.text


def test_api_rejects_invalid_ids_and_unknown_resources(client: TestClient) -> None:
    invalid = client.get("/api/v1/designs/..%2Foutside")
    missing = client.get("/api/v1/designs/missing_design")

    assert invalid.status_code in {400, 404}
    assert missing.status_code == 404


def test_export_blocks_incomplete_genbank(client: TestClient) -> None:
    draft = ImportDraft.empty()
    payload = _draft_payload("incomplete_export")
    payload["parts"][1]["sequence"] = None
    assert client.post(
        "/api/v1/imports/json",
        json={"draft": payload},
    ).status_code == 201
    assert client.post(
        "/api/v1/imports/incomplete_export/confirm"
    ).status_code == 201

    response = client.get(
        "/api/v1/designs/incomplete_export/exports/genbank"
    )

    assert draft.draft_id.startswith("external_")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EXPORT_BLOCKED"


def test_request_validation_uses_stable_error_contract(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/genbank",
        json={"filename": "empty.gb", "content": ""},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"


def test_run_api_contract_without_executing_llm(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = client.app.state.test_services
    monkeypatch.setattr(
        services.runs,
        "start",
        lambda request: {
            "run_id": "run_api_test",
            "status": "queued",
            "progress": 0.0,
            "request_model": request.get("model_name"),
            "request_cello_artifact_format": request.get("cello_artifact_format"),
        },
    )
    monkeypatch.setattr(
        services.runs,
        "status",
        lambda run_id: {
            "run_id": run_id,
            "status": "running",
            "stage": "builder",
            "progress": 0.25,
        },
    )
    monkeypatch.setattr(
        services.runs,
        "events",
        lambda run_id, after_event_id=0, limit=100: {
            "run_id": run_id,
            "status": "running",
            "events": [
                {
                    "event_id": 1,
                    "stage": "builder",
                    "status": "running",
                    "progress": 0.25,
                    "message": "Generating logic proposals.",
                }
            ],
        },
    )

    started = client.post(
        "/api/v1/runs",
        json={
            "user_intent": "Express GFP when A is present.",
            "model_name": "test-model",
            "cello_artifact_format": "cello21",
        },
    )
    status_response = client.get("/api/v1/runs/run_api_test")
    events = client.get("/api/v1/runs/run_api_test/events")

    assert started.status_code == 202
    assert started.json()["data"]["run_id"] == "run_api_test"
    assert started.json()["data"]["request_cello_artifact_format"] == "cello21"
    assert status_response.json()["data"]["progress"] == 0.25
    assert events.json()["data"]["events"][0]["stage"] == "builder"


def test_resume_api_preserves_explicit_cello21_format(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = client.app.state.test_services
    captured = {}

    def fake_resume(run_id, **kwargs):
        captured["run_id"] = run_id
        captured.update(kwargs)
        return {"run_id": "run_child", "status": "queued"}

    monkeypatch.setattr(services.runs, "resume", fake_resume)
    response = client.post(
        "/api/v1/runs/run_parent/resume",
        json={"cello_artifact_format": "cello21"},
    )
    assert response.status_code == 202
    assert captured["cello_artifact_format"] == "cello21"


def test_resume_api_preserves_omitted_format_for_parent_resolution(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = client.app.state.test_services
    captured = {}

    def fake_resume(run_id, **kwargs):
        captured["run_id"] = run_id
        captured.update(kwargs)
        return {"run_id": "run_child", "status": "queued"}

    monkeypatch.setattr(services.runs, "resume", fake_resume)
    response = client.post("/api/v1/runs/run_parent/resume", json={})
    assert response.status_code == 202
    assert captured["cello_artifact_format"] is None


def test_run_api_rejects_path_traversal_ids(client: TestClient) -> None:
    response = client.get("/api/v1/runs/..%2Foutside")

    assert response.status_code in {400, 404}


def test_run_service_does_not_forward_browser_api_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_start_design_run(**kwargs):
        captured.update(kwargs)
        return {"run_id": "run_safe", "status": "queued"}

    monkeypatch.setattr(
        "application.services.start_design_run",
        fake_start_design_run,
    )
    services = create_application_services(tmp_path / "api_data")

    result = services.runs.start(
        {
            "user_intent": "Test",
            "api_key": "must-not-be-forwarded",
        }
    )

    assert result["run_id"] == "run_safe"
    assert "api_key" not in captured


def test_server_rendered_web_pages_are_available(client: TestClient) -> None:
    expected = {
        "/web": "研究工作台",
        "/web/new-design": "建立新設計",
        "/web/runs": "設計執行",
        "/web/imports": "外部設計導入",
        "/web/designs": "設計資料庫",
        "/web/compare": "設計比較",
    }
    for path, text in expected.items():
        response = client.get(path)
        assert response.status_code == 200
        assert text in response.text


def test_guided_web_import_review_and_confirm(client: TestClient) -> None:
    created = client.post(
        "/web/imports/guided",
        data={
            "name": "Web imported circuit",
            "source_type": "literature",
            "source_uri": "https://doi.org/10.0000/web",
            "host_organism": "Escherichia coli",
            "inputs": "A, B",
            "outputs": "GFP",
            "logic_expression": "GFP = A AND NOT B",
            "validation_status": "experimentally_validated",
            "evidence_status": "explicit",
        },
        follow_redirects=False,
    )

    assert created.status_code == 303
    review_url = created.headers["location"]
    review = client.get(review_url)
    assert review.status_code == 200
    assert "Web imported circuit" in review.text
    assert "確認並建立 DesignIR" in review.text

    draft_id = review_url.rsplit("/", 1)[-1]
    confirmed = client.post(
        f"/web/imports/{draft_id}/confirm",
        follow_redirects=False,
    )
    assert confirmed.status_code == 303
    detail = client.get(confirmed.headers["location"])
    assert detail.status_code == 200
    assert "Web imported circuit" in detail.text


def test_web_run_form_redirects_to_background_run(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = client.app.state.test_services
    captured_request = {}
    monkeypatch.setattr(
        services.settings,
        "get_settings_masked",
        lambda: {"model_credentials_configured": True},
    )

    def fake_start(request):
        captured_request.update(request)
        return {
            "run_id": "run_web_test",
            "status": "queued",
            "progress": 0.0,
        }

    monkeypatch.setattr(
        services.runs,
        "start",
        fake_start,
    )

    response = client.post(
        "/web/runs",
        data={
            "user_intent": "Express GFP.",
            "host_organism": "Escherichia coli",
            "compute_budget": "3",
            "model_name": "custom-model",
            "api_base": "https://models.example/v1",
            "enable_rag": "on",
            "enable_ode": "on",
            "enable_skill_extraction": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/web/runs/run_web_test"
    assert captured_request == {
        "user_intent": "Express GFP.",
        "host_organism": "Escherichia coli",
        "compute_budget": 3,
        "model_name": "custom-model",
        "api_base": "https://models.example/v1",
        "enable_rag": True,
        "enable_ode": True,
        "enable_skill_extraction": True,
    }


def test_web_run_requires_model_credentials(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = client.app.state.test_services
    monkeypatch.setattr(
        services.settings,
        "get_settings_masked",
        lambda: {"model_credentials_configured": False},
    )

    response = client.post(
        "/web/runs",
        data={"user_intent": "Express GFP."},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/web/settings?credentials_required=1"


def test_web_run_detail_rejects_invalid_run_id_without_internal_error(
    client: TestClient,
) -> None:
    response = client.get("/web/runs/not-a-real-run")

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "HTTP_ERROR",
            "message": "Invalid run ID.",
            "details": [],
        }
    }
    assert "Traceback" not in response.text
    assert "C:\\Users\\" not in response.text


def test_new_design_surfaces_preflight_without_false_draft_banner(
    client: TestClient,
) -> None:
    response = client.get("/web/new-design")

    assert response.status_code == 200
    assert "啟動 AI 設計前必須先設定" in response.text
    assert 'id="btn-wizard-submit"' in response.text
    assert "disabled aria-disabled=\"true\"" in response.text
    assert response.text.count("display: none;") >= 1
    assert "display: none; background" in response.text

    app_js = (
        Path(__file__).resolve().parent.parent / "src" / "web" / "static" / "app.js"
    ).read_text(encoding="utf-8")
    assert "structured_spec.copy_number" not in app_js


def test_web_run_retry_preserves_execution_contract(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = client.app.state.test_services
    captured_request = {}
    original_request = {
        "user_intent": "Express GFP.",
        "host_organism": "Escherichia coli",
        "compute_budget": 9,
        "model_name": "custom-model",
        "api_base": "https://models.example/v1",
        "enable_rag": False,
        "enable_ode": True,
        "enable_skill_extraction": False,
        "monte_carlo_samples": 4,
    }
    monkeypatch.setattr(
        services.runs,
        "status",
        lambda run_id: {"run_id": run_id, "request": original_request},
    )

    def fake_start(request):
        captured_request.update(request)
        return {"run_id": "run_retry_contract", "status": "queued"}

    monkeypatch.setattr(services.runs, "start", fake_start)

    response = client.post(
        "/web/runs/run_original/retry",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/web/runs/run_retry_contract"
    assert captured_request == original_request


def test_web_run_detail_monitor_endpoint(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = client.app.state.test_services
    monkeypatch.setattr(
        services.runs,
        "status",
        lambda run_id: {
            "run_id": run_id,
            "status": "completed",
            "stage": "consolidator",
            "progress": 1.0,
        },
    )
    monkeypatch.setattr(
        services.runs,
        "events",
        lambda run_id, after_event_id=0, limit=100: {
            "run_id": run_id,
            "events": [
                {
                    "event_id": 2,
                    "stage": "consolidator",
                    "status": "completed",
                    "message": "Final design assembled.",
                }
            ],
        },
    )
    monkeypatch.setattr(
        services.runs,
        "result",
        lambda run_id: {
            "run_id": run_id,
            "status": "completed",
            "design_id": "design_web_test",
            "summary": {
                "best_topology": {
                    "score": 0.91,
                    "mapping_status": "mapped",
                    "ode_status": "completed",
                    "verilog": (
                        "module genetic_circuit(input A, input B, output Y); "
                        "assign Y = A & ~B; endmodule"
                    ),
                    "semantic_faithfulness_score": 0.95,
                    "robustness_score": 0.81,
                    "toxicity_score": 0.88,
                    "dynamic_margin": 0.42,
                    "ode_trace": {
                        "time": [0, 1, 2],
                        "output_protein": [0, 4, 8],
                        "total_mrna": [0, 1, 2],
                        "rnap_occupancy": [0.1, 0.2, 0.3],
                    },
                }
            },
            "tree_summary": [
                {
                    "node_id": "root",
                    "parent_id": None,
                    "search_mode": "Exploration",
                    "status": "Evaluated",
                    "score": 0.71,
                    "error_type": "NONE",
                    "critic_feedback": "Initial candidate was viable.",
                },
                {
                    "node_id": "repair_1",
                    "parent_id": "root",
                    "search_mode": "Repair",
                    "status": "Pass",
                    "score": 0.91,
                    "error_type": "NONE",
                    "critic_feedback": "Repair improved robustness.",
                },
            ],
            "current_node_id": "repair_1",
        },
    )

    page = client.get("/web/runs/run_web_test")
    status = client.get("/web/runs/run_web_test/status")
    history_page = client.get("/web/runs/run_web_test/decision-history")

    assert page.status_code == 200
    assert "Design run monitor" in page.text
    assert "Event timeline" in page.text
    assert "Latest message" in page.text

    assert history_page.status_code == 200
    assert "Score Breakdown" in history_page.text or "score_breakdown" in history_page.text
    assert "semantic_faithfulness_score" in history_page.text or "Semantic faithfulness" in history_page.text
    assert "verilog" in history_page.text
    assert "ode_trace" in history_page.text
    assert "tree_summary" in history_page.text
    assert "output_protein" in history_page.text
    assert "repair_1" in history_page.text
    assert "Payload" in history_page.text or "payload" in history_page.text
    assert status.status_code == 200
    payload = status.json()
    assert payload["run"]["progress"] == 1.0
    assert payload["events"][0]["stage"] == "consolidator"
    assert payload["terminal"] is True
    assert payload["monitor"]["status_class"] == "status-ready"
    assert payload["monitor"]["latest_message"] == "Final design assembled."
    assert payload["monitor"]["score_summary"]["score"] == 0.91
    assert payload["monitor"]["score_breakdown"][0]["key"] == (
        "semantic_faithfulness_score"
    )
    assert payload["monitor"]["topology_graph"]["nodes"][0]["type"] == "logic"
    assert payload["monitor"]["ode_trace"]["series"][0]["key"] == "output_protein"
    assert payload["monitor"]["search_tree"]["path"][-1]["node_id"] == "repair_1"


def test_web_interaction_enhancements_are_available(client: TestClient) -> None:
    research = client.get("/web/research?lang=en")
    assembly = client.get("/web/assembly")
    assembly_backbones = client.get("/web/assembly/backbones")
    assembly_new = client.get("/web/assembly/new")
    app_js = (Path(__file__).resolve().parent.parent / "src" / "web" / "static" / "app.js").read_text(encoding="utf-8")

    assert research.status_code == 200
    assert assembly.status_code == 200
    assert assembly_backbones.status_code == 200
    assert assembly_new.status_code == 200
    assert "Assembly Delivery" in assembly.text
    assert "Backbone Registry" in assembly_backbones.text
    assert "New Assembly Deliverable" in assembly_new.text
    assert 'name="truth_table_json" data-json-input="array"' in research.text
    assert (
        'name="essential_regions_json" data-json-input="array"'
        in assembly_backbones.text
    )
    assert "validateJsonField" in app_js
    assert "aria-busy" in app_js
    assert "data-run-latest-message" in app_js
    assert "status-badge" in app_js
    assert "Please fix invalid JSON fields before submitting." in app_js


def test_web_design_detail_tabs_and_clipboard(client: TestClient) -> None:
    services = client.app.state.test_services
    draft = services.imports.import_json(_draft_payload("external_tabs_test"))
    draft_id = draft.draft_id

    confirmed = client.post(
        f"/web/imports/{draft_id}/confirm",
        follow_redirects=False,
    )
    assert confirmed.status_code == 303
    design_url = confirmed.headers["location"]

    response = client.get(design_url)
    assert response.status_code == 200

    assert "邏輯設計規格" in response.text or "設計合成就緒評估" in response.text
    assert "材料元件清單" in response.text
    assert "裝配文件與交付下載" in response.text
    assert "data-copy-text" in response.text
    assert "switchTab" in response.text
    assert "NCBI" in response.text
    assert "SynBioHub" in response.text
    assert "BOM 材料 CSV 明細" in response.text
    assert "GenBank 完整圖譜" in response.text
    assert f"/web/designs/{draft_id}/optimize/codon" in response.text
    assert "/optimize/rbs" not in response.text
    assert response.text.count("HTML 工作流程尚未提供") == 3

    app_js = (Path(__file__).resolve().parent.parent / "src" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "setupClipboardCopy" in app_js


def test_web_codon_optimization_review_and_revision_flow(
    client: TestClient,
) -> None:
    services = client.app.state.test_services
    payload = _draft_payload("codon_ui_design")
    payload["parts"][1]["sequence"] = "ATGGGTCTCTAA"
    draft = services.imports.import_json(payload)
    design = services.imports.confirm_by_id(draft.draft_id)
    path = f"/web/designs/{design.design_id}/optimize/codon"

    review = client.get(path)

    assert review.status_code == 200
    assert "計算篩選邊界" in review.text
    assert "可最佳化 CDS" in review.text
    assert "建立最佳化 revision" in review.text
    assert len(services.designs.revisions(design.design_id)) == 1

    missing_confirmation = client.post(
        path,
        data={
            "host_profile_id": "ecoli_k12_default",
            "confirm_design_id": design.design_id,
        },
    )
    assert missing_confirmation.status_code == 422
    assert len(services.designs.revisions(design.design_id)) == 1

    mismatched_confirmation = client.post(
        path,
        data={
            "host_profile_id": "ecoli_k12_default",
            "confirm": "on",
            "confirm_design_id": "wrong-design",
        },
    )
    assert mismatched_confirmation.status_code == 403
    assert len(services.designs.revisions(design.design_id)) == 1

    created = client.post(
        path,
        data={
            "host_profile_id": "ecoli_k12_default",
            "confirm": "on",
            "confirm_design_id": design.design_id,
        },
        follow_redirects=False,
    )

    assert created.status_code == 303
    assert created.headers["location"].startswith(
        f"/web/designs/{design.design_id}?rev=2"
    )
    revisions = services.designs.revisions(design.design_id)
    assert len(revisions) == 2
    optimized = services.designs.get_revision(design.design_id, 2)
    assert optimized is not None
    assert optimized.revision.change_type == "sequence_optimization"
