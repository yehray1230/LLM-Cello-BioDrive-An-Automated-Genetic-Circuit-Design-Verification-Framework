from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def _run_isolated_import(code: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), str(ROOT / "src"), env.get("PYTHONPATH", "")]
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_application_package_import_does_not_eagerly_load_services() -> None:
    result = _run_isolated_import(
        "\n".join(
            [
                "import sys",
                "import application",
                "assert 'application.services' not in sys.modules",
                "assert 'ApplicationServices' in application.__all__",
            ]
        )
    )
    assert result.returncode == 0, result.stderr


def test_temporal_schema_import_does_not_load_unrelated_or_native_modules() -> None:
    result = _run_isolated_import(
        "\n".join(
            [
                "import sys",
                "from schemas import DEFAULT_TEMPORAL_CONFIG, PhaseWindow",
                "assert DEFAULT_TEMPORAL_CONFIG.version",
                "assert PhaseWindow.__name__ == 'PhaseWindow'",
                "assert 'schemas.temporal_evaluation' in sys.modules",
                "assert 'schemas.design_diff' not in sys.modules",
                "assert not any(name == 'Bio' or name.startswith('Bio.') for name in sys.modules)",
            ]
        )
    )
    assert result.returncode == 0, result.stderr


def test_design_task_evaluator_import_stays_on_the_pure_python_path() -> None:
    result = _run_isolated_import(
        "\n".join(
            [
                "import sys",
                "from application.design_task_benchmark import _evaluate_combinational_task",
                "assert callable(_evaluate_combinational_task)",
                "assert 'application.services' not in sys.modules",
                "assert not any(name == 'Bio' or name.startswith('Bio.') for name in sys.modules)",
            ]
        )
    )
    assert result.returncode == 0, result.stderr
