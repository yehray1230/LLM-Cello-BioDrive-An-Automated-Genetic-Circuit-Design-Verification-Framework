"""Stage F verification tests.

Covers:
- Assembly deliverables correctly bind revision_number.
- Export requests accept specific revisions.
- Share summary masks local file paths and credentials.
- ZIP project package generates valid archive with manifest.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_services
from api.main import app
from application.services import create_application_services
from schemas.design_ir_v2 import (
    BiologicalPartV2,
    DesignIRV2,
    DesignSpecification,
    ConstructV2,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_services(tmp_path: Path):
    return create_application_services(tmp_path / "api_data")


@pytest.fixture
def client(test_services):
    app.dependency_overrides[get_services] = lambda: test_services
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()


def _sample_design(design_id: str = "stage_f_test") -> DesignIRV2:
    """Build a minimal but realistic design for Stage F tests."""
    return DesignIRV2(
        design_id=design_id,
        name="Stage F Test Design",
        specification=DesignSpecification(
            inputs=["arabinose"],
            outputs=["GFP"],
        ),
        parts=[
            BiologicalPartV2(
                id="p_ara",
                name="pAra",
                part_type="promoter",
                role="sensor",
                sequence="ATGCATGC",
                evidence_level="experimentally_characterized",
            ),
            BiologicalPartV2(
                id="gfp_cds",
                name="GFP",
                part_type="CDS",
                role="reporter",
                sequence="ATGAGTAAAGGAGAAGAACTTTTCACTGGAGTTGTC",
                evidence_level="experimentally_characterized",
            ),
        ],
        interactions=[],
        constructs=[
            ConstructV2(
                id="c_gfp",
                name="GFP_Expression",
                topology="linear",
                part_instances=[],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 1. Assembly deliverable binds revision_number
# ---------------------------------------------------------------------------


def test_assembly_deliverable_binds_revision(test_services):
    """Creating an assembly deliverable should persist the revision_number."""
    design_id = "rev_bind_test"
    design_v2 = _sample_design(design_id)
    test_services.designs.save_v2(design_v2)

    # Modify and save again to create revision 2
    design_v2.parts.append(
        BiologicalPartV2(
            id="term_1",
            name="T1",
            part_type="terminator",
            role="termination",
            sequence="GCCGCC",
            evidence_level="literature_supported",
        ),
    )
    test_services.designs.save_v2(design_v2)

    revisions = test_services.designs.revisions(design_id)
    assert len(revisions) >= 2


# ---------------------------------------------------------------------------
# 2. Export with revision parameter
# ---------------------------------------------------------------------------


def test_export_json_with_revision(client, test_services):
    """JSON export should work and accept a revision parameter."""
    design_id = "export_json_test"
    design_v2 = _sample_design(design_id)
    test_services.designs.save_v2(design_v2)

    response = client.get(
        f"/api/v1/designs/{design_id}/exports/json?rev=1"
    )
    assert response.status_code == 200
    data = json.loads(response.content)
    assert data.get("design_id") == design_id
    assert response.headers["x-source-revision"] == "1"
    assert response.headers["x-source-revision-id"].endswith("revision_1")
    assert response.headers["x-export-warning-count"] == "0"


def test_export_bom_csv(client, test_services):
    """BOM CSV export should return a downloadable file."""
    design_id = "export_bom_test"
    design_v2 = _sample_design(design_id)
    test_services.designs.save_v2(design_v2)

    response = client.get(f"/api/v1/designs/{design_id}/exports/bom")
    assert response.status_code == 200
    assert "text/csv" in response.headers.get("content-type", "")
    assert response.headers["x-claim-boundary"] == "computational-exchange-artifact-only"
    assert response.headers["x-not-wet-lab-validation"] == "true"


def test_export_genbank(client, test_services):
    """GenBank export should return downloadable content."""
    design_id = "export_gb_test"
    design_v2 = _sample_design(design_id)
    test_services.designs.save_v2(design_v2)

    response = client.get(f"/api/v1/designs/{design_id}/exports/genbank")
    assert response.status_code == 200
    assert response.headers["x-claim-boundary"] == "computational-exchange-artifact-only"
    assert response.headers["x-not-experimental-protocol"] == "true"


def test_export_sbol3(client, test_services):
    """SBOL3 export should return downloadable content."""
    design_id = "export_sbol_test"
    design_v2 = _sample_design(design_id)
    test_services.designs.save_v2(design_v2)

    response = client.get(f"/api/v1/designs/{design_id}/exports/sbol3")
    assert response.status_code == 200
    assert response.headers["x-claim-boundary"] == "computational-exchange-artifact-only"
    assert response.headers["x-biophysical-uncertainty"] == "requires-review"


def test_export_verilog(client, test_services):
    """Verilog export should return content or a graceful error."""
    design_id = "export_verilog_test"
    design_v2 = _sample_design(design_id)
    test_services.designs.save_v2(design_v2)

    response = client.get(f"/api/v1/designs/{design_id}/exports/verilog")
    assert response.status_code == 409
    assert "EXPORT_BLOCKED" in response.text


# ---------------------------------------------------------------------------
# 3. Share summary masks secrets and paths
# ---------------------------------------------------------------------------


def test_share_summary_renders(client, test_services):
    """The share summary page should render HTML."""
    design_id = "share_test"
    design_v2 = _sample_design(design_id)
    test_services.designs.save_v2(design_v2)

    response = client.get(f"/web/designs/{design_id}/share_summary")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "已執行的計算檢查（非實驗驗證）" in response.text
    assert "此研究預覽未經濕實驗驗證" in response.text
    assert "已實證與驗證項目 (Verified)" not in response.text
    assert "pUC19-J23100-sfGFP" not in response.text
    assert "Cello UCF Library (Eco1C1G1T1)" not in response.text


def test_share_summary_masks_paths_and_nested_credentials():
    """The production sanitizer handles paths and nested sensitive metadata."""
    from web.routes import _sanitize_shareable

    sanitized = _sanitize_shareable(
        {
            "source": r"C:\Users\lab\data\project.json",
            "nested": {"api_key": "sk-123", "note": "token=abc123"},
        }
    )
    assert sanitized["source"] == "[local_path]"
    assert sanitized["nested"]["api_key"] == "[hidden]"
    assert "abc123" not in sanitized["nested"]["note"]


def test_share_summary_does_not_contain_local_paths(client, test_services):
    """Rendered share summary should not contain Windows absolute paths."""
    import re

    design_id = "share_paths_test"
    design_v2 = _sample_design(design_id)
    # Inject a path into metadata
    design_v2.parts[0].metadata["source_file"] = r"C:\Users\dev\project\part.gbk"
    test_services.designs.save_v2(design_v2)

    response = client.get(f"/web/designs/{design_id}/share_summary")
    assert response.status_code == 200
    body = response.text
    # Should not find raw Windows paths — only [local_path] placeholders
    raw_paths = re.findall(r"[A-Za-z]:\\[^\s\"'<>]+\\[^\s\"'<>]+", body)
    assert len(raw_paths) == 0, f"Found unmasked paths: {raw_paths}"


# ---------------------------------------------------------------------------
# 4. ZIP project package
# ---------------------------------------------------------------------------


def test_project_package_returns_zip(client, test_services):
    """The project package endpoint should return a valid ZIP archive."""
    design_id = "pkg_test"
    design_v2 = _sample_design(design_id)
    test_services.designs.save_v2(design_v2)

    response = client.get(f"/web/designs/{design_id}/exports/project_package")
    assert response.status_code == 200
    content_type = response.headers.get("content-type", "")
    assert "zip" in content_type or "octet-stream" in content_type

    # Verify it's a valid ZIP
    buf = io.BytesIO(response.content)
    with zipfile.ZipFile(buf, "r") as zf:
        names = zf.namelist()
        assert len(names) > 0

        # Should contain manifest.json
        assert "manifest.json" in names
        assert "CLAIM_BOUNDARY.md" in names
        assert "CLAIM_BOUNDARY.json" in names
        manifest = json.loads(zf.read("manifest.json"))
        assert "design_id" in manifest or "files" in manifest
        assert manifest["revision_id"].endswith("revision_1")
        assert manifest["revision_number"] == 1
        file_names = {item["filename"] for item in manifest["files"]}
        assert {"CLAIM_BOUNDARY.md", "CLAIM_BOUNDARY.json"}.issubset(file_names)
        claim_boundary = zf.read("CLAIM_BOUNDARY.md").decode("utf-8")
        assert "Computational exchange artifact only" in claim_boundary
        assert "not wet-lab validation" in claim_boundary
        assert "not an experimental protocol" in claim_boundary


def test_project_package_contains_design_json(client, test_services):
    """The project package should include a design JSON file."""
    design_id = "pkg_json_test"
    design_v2 = _sample_design(design_id)
    test_services.designs.save_v2(design_v2)

    response = client.get(f"/web/designs/{design_id}/exports/project_package")
    assert response.status_code == 200

    buf = io.BytesIO(response.content)
    with zipfile.ZipFile(buf, "r") as zf:
        names = zf.namelist()
        json_files = [n for n in names if n.endswith(".json") and n != "manifest.json"]
        assert len(json_files) >= 1, f"Expected at least one design JSON file, got: {names}"


# ---------------------------------------------------------------------------
# 5. Design detail page loads with export center tab
# ---------------------------------------------------------------------------


def test_design_detail_has_export_tab(client, test_services):
    """Design detail page should have the Export Center tab."""
    design_id = "export_tab_test"
    design_v2 = _sample_design(design_id)
    test_services.designs.save_v2(design_v2)

    response = client.get(f"/web/designs/{design_id}")
    assert response.status_code == 200
    body = response.text
    assert "匯出中心" in body or "Export Center" in body or "tab-export" in body


def test_design_detail_has_revision_history(client, test_services):
    """Design detail page should show revision history tab."""
    design_id = "rev_history_test"
    design_v2 = _sample_design(design_id)
    test_services.designs.save_v2(design_v2)

    response = client.get(f"/web/designs/{design_id}")
    assert response.status_code == 200
    body = response.text
    assert "版本歷程" in body or "tab-revisions" in body or "revisions" in body
