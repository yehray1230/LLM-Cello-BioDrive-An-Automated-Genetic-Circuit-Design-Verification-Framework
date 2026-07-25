from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.dependencies import get_services
from api.main import app
from application.services import create_application_services
from repositories.factory import create_design_repository, repository_backend
from repositories.sqlite_repository import SqliteDesignRepository


def _request() -> dict:
    return {
        "topology": {
            "verilog": (
                "module c(input A, output Y); assign Y = A; endmodule"
            ),
            "truth_table": [
                {"A": 0, "Y": 0},
                {"A": 1, "Y": 1},
            ],
            "copy_number": 3,
        },
        "simulation_time": 30,
        "sample_count": 8,
        "monte_carlo_samples": 1,
        "random_seed": 19,
        "profile_id": "research-v2-preview",
    }


def _wait(services, run_id: str) -> dict:
    services.research.run_store._futures[run_id].result(timeout=30)
    return services.research.result(run_id)


def test_research_service_runs_simulation_evaluation_and_reports(
    tmp_path: Path,
) -> None:
    services = create_application_services(tmp_path / "api_data")

    started = services.research.start_simulation(_request())
    result = _wait(services, started["run_id"])

    assert result["status"] == "completed"
    assert result["simulation_result"]["model_version"] == "1.10.0"
    assert result["evaluation"]["scoring_version"] == "1.9.0"
    assert result["research_result_hash"]
    for path in result["artifacts"].values():
        assert Path(path).is_file()
    manifest = json.loads(
        Path(started["run_manifest_path"]).read_text(encoding="utf-8")
    )
    assert manifest["simulation"]["configuration_hash"]


def test_research_comparison_ranks_compatible_runs(tmp_path: Path) -> None:
    services = create_application_services(tmp_path / "api_data")
    first = services.research.start_simulation(_request())
    changed = _request()
    changed["topology"]["copy_number"] = 8
    second = services.research.start_simulation(changed)
    _wait(services, first["run_id"])
    _wait(services, second["run_id"])

    comparison = services.research.compare(
        [first["run_id"], second["run_id"]]
    )

    assert comparison["comparable"] is True
    assert len(comparison["ranked_runs"]) == 2
    assert comparison["ranked_runs"][0]["rank"] == 1
    assert comparison["comparison_hash"]


def test_research_comparison_warns_when_versions_differ(
    tmp_path: Path,
    monkeypatch,
) -> None:
    services = create_application_services(tmp_path / "api_data")
    results = {
        "research_a": {
            "status": "completed",
            "simulation_result": {"model_version": "1.9.0"},
            "evaluation": {
                "weighted_total_score": 0.8,
                "grade": "pass",
                "scoring_profile": "research-v2-preview",
                "scoring_version": "1.9.0",
            },
        },
        "research_b": {
            "status": "completed",
            "simulation_result": {"model_version": "2.0.0"},
            "evaluation": {
                "weighted_total_score": 0.9,
                "grade": "pass",
                "scoring_profile": "future",
                "scoring_version": "2.0.0",
            },
        },
    }
    monkeypatch.setattr(
        services.research,
        "result",
        lambda run_id: results[run_id],
    )

    comparison = services.research.compare(["research_a", "research_b"])

    assert comparison["comparable"] is False
    assert comparison["warning"]


def test_v2_api_runs_and_downloads_research_artifact(tmp_path: Path) -> None:
    services = create_application_services(tmp_path / "api_data")
    app.dependency_overrides[get_services] = lambda: services
    try:
        with TestClient(app) as client:
            health = client.get("/api/v2/health")
            started = client.post("/api/v2/research/runs", json=_request())
            run_id = started.json()["data"]["run_id"]
            _wait(services, run_id)
            status = client.get(f"/api/v2/research/runs/{run_id}")
            result = client.get(f"/api/v2/research/runs/{run_id}/result")
            artifact = client.get(
                f"/api/v2/research/runs/{run_id}/artifacts/summary_markdown"
            )
    finally:
        app.dependency_overrides.clear()

    assert health.json()["data"]["storage_backend"] == "sqlite"
    assert started.status_code == 202
    assert status.json()["data"]["status"] == "completed"
    assert result.json()["data"]["evaluation"]["scoring_version"] == "1.9.0"
    assert artifact.status_code == 200
    assert "Research Simulation Report" in artifact.text


def test_v2_research_api_hides_internal_artifact_paths(tmp_path: Path) -> None:
    services = create_application_services(tmp_path / "api_data")
    app.dependency_overrides[get_services] = lambda: services
    try:
        with TestClient(app) as client:
            started = client.post("/api/v2/research/runs", json=_request())
            run_id = started.json()["data"]["run_id"]
            _wait(services, run_id)
            status = client.get(f"/api/v2/research/runs/{run_id}")
            result = client.get(f"/api/v2/research/runs/{run_id}/result")
            listed = client.get("/api/v2/research/runs")
            artifact = client.get(
                f"/api/v2/research/runs/{run_id}/artifacts/summary_markdown"
            )
    finally:
        app.dependency_overrides.clear()

    for response in (started, status, result, listed):
        assert response.status_code in {200, 202}
        body = json.dumps(response.json(), sort_keys=True)
        assert "C:\\\\" not in body
        assert "run_dir" not in body
        assert "result_path" not in body
        assert "run_manifest_path" not in body
        assert "async_run_dir" not in body

    result_data = result.json()["data"]
    assert "artifact_links" in result_data
    assert "summary_markdown" in result_data["artifact_links"]
    assert result_data["artifact_links"]["summary_markdown"].endswith(
        "/artifacts/summary_markdown"
    )
    assert artifact.status_code == 200
    assert "Research Simulation Report" in artifact.text


def test_research_workspace_pages_and_form(tmp_path: Path) -> None:
    services = create_application_services(tmp_path / "api_data")
    app.dependency_overrides[get_services] = lambda: services
    try:
        with TestClient(app) as client:
            page = client.get("/web/research?lang=en")
            compare = client.get("/web/research/compare")
            started = client.post(
                "/web/research/runs",
                data={
                    "verilog": _request()["topology"]["verilog"],
                    "truth_table_json": json.dumps(
                        _request()["topology"]["truth_table"]
                    ),
                    "copy_number": "3",
                    "simulation_time": "30",
                    "sample_count": "8",
                    "monte_carlo_samples": "1",
                    "noise_fraction": "0.15",
                    "random_seed": "19",
                    "profile_id": "research-v2-preview",
                },
                follow_redirects=False,
            )
            run_id = started.headers["location"].rsplit("/", 1)[-1]
            _wait(services, run_id)
            detail = client.get(started.headers["location"])
            detail_zh = client.get(f"{started.headers['location']}?lang=zh-Hant")
    finally:
        app.dependency_overrides.clear()

    assert page.status_code == 200
    assert "Research Workspace" in page.text
    assert compare.status_code == 200
    assert started.status_code == 303
    assert "Evaluation dimensions" in detail.text
    assert "評估維度" in detail_zh.text
    assert "Evaluation dimensions" not in detail_zh.text


def test_repository_factory_defaults_to_sqlite(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("GENETIC_CIRCUIT_DATABASE_URL", raising=False)
    repository = create_design_repository(tmp_path / "research.db")

    assert isinstance(repository, SqliteDesignRepository)
    assert repository_backend(repository) == "sqlite"


def test_repository_factory_selects_postgres_url(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class StubPostgres:
        def __init__(self, database_url: str):
            self.database_url = database_url

    monkeypatch.setattr(
        "repositories.factory.PostgresDesignRepository",
        StubPostgres,
    )
    repository = create_design_repository(
        tmp_path / "unused.db",
        "postgresql://research:test@localhost/research",
    )

    assert isinstance(repository, StubPostgres)
    assert repository.database_url.startswith("postgresql://")
