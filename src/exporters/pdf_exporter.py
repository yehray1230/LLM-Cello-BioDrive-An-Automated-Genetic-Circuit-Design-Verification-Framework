from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import logging
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

ReportGenerationStatus = Literal[
    "native_pdf",
    "print_html_fallback",
    "failed",
]


@dataclass(frozen=True)
class ReportArtifactResult:
    """Truthful outcome of one report-rendering attempt."""

    requested_path: Path
    artifact_path: Path | None
    generation_status: ReportGenerationStatus
    media_type: str | None
    renderer: str
    renderer_version: str | None = None
    fallback_reason: str | None = None
    error_type: str | None = None

    @property
    def generated_pdf(self) -> bool:
        return self.generation_status == "native_pdf"

    def to_manifest_entry(self) -> dict[str, str]:
        if self.artifact_path is None or not self.artifact_path.is_file():
            raise RuntimeError("Cannot create a manifest entry without a report artifact.")
        if self.media_type is None:
            raise RuntimeError("Cannot create a manifest entry without a media type.")

        entry = {
            "filename": self.artifact_path.name,
            "media_type": self.media_type,
            "generation_status": self.generation_status,
            "renderer": self.renderer,
            "sha256": sha256(self.artifact_path.read_bytes()).hexdigest(),
        }
        if self.renderer_version:
            entry["renderer_version"] = self.renderer_version
        if self.fallback_reason:
            entry["fallback_reason"] = self.fallback_reason
        if self.error_type:
            entry["renderer_error_type"] = self.error_type
        return entry


def export_report_artifact(
    html_content: str,
    output_pdf_path: Path | str,
) -> ReportArtifactResult:
    """Render a PDF or return an explicitly labelled print-HTML fallback."""
    output_path = Path(output_pdf_path)
    fallback_reason: str | None = None
    renderer_error_type: str | None = None
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return ReportArtifactResult(
            requested_path=output_path,
            artifact_path=None,
            generation_status="failed",
            media_type=None,
            renderer="none",
            fallback_reason="output_directory_unavailable",
            error_type=type(exc).__name__,
        )

    try:
        import weasyprint  # type: ignore

        weasyprint.HTML(string=html_content).write_pdf(str(output_path))
        if output_path.is_file():
            logger.info("Successfully generated PDF via WeasyPrint: %s", output_path)
            return ReportArtifactResult(
                requested_path=output_path,
                artifact_path=output_path,
                generation_status="native_pdf",
                media_type="application/pdf",
                renderer="weasyprint",
                renderer_version=getattr(weasyprint, "__version__", None),
            )
        fallback_reason = "renderer_missing_output"
        logger.warning(
            "WeasyPrint returned without producing the requested PDF: %s",
            output_path,
        )
    except ImportError as exc:
        fallback_reason = "renderer_unavailable"
        renderer_error_type = type(exc).__name__
        logger.info("WeasyPrint is unavailable: %s. Using print-HTML fallback.", exc)
    except Exception as exc:
        fallback_reason = "renderer_failed"
        renderer_error_type = type(exc).__name__
        logger.warning("WeasyPrint failed: %s. Using print-HTML fallback.", exc)

    fallback_html_path = output_path.with_suffix(".print.html")
    try:
        print_ready_html = _inject_auto_print_script(html_content)
        fallback_html_path.write_text(print_ready_html, encoding="utf-8")
        logger.info("Generated print-ready HTML fallback at: %s", fallback_html_path)
        return ReportArtifactResult(
            requested_path=output_path,
            artifact_path=fallback_html_path,
            generation_status="print_html_fallback",
            media_type="text/html",
            renderer="browser_print",
            fallback_reason=fallback_reason,
            error_type=renderer_error_type,
        )
    except OSError as exc:
        logger.error("Could not write print-HTML fallback: %s", exc)
        return ReportArtifactResult(
            requested_path=output_path,
            artifact_path=None,
            generation_status="failed",
            media_type=None,
            renderer="none",
            fallback_reason="fallback_write_failed",
            error_type=type(exc).__name__,
        )


def export_report_to_pdf(
    html_content: str,
    output_pdf_path: Path | str,
) -> bool:
    """Backward-compatible boolean wrapper around :func:`export_report_artifact`."""
    result = export_report_artifact(html_content, output_pdf_path)
    if result.generation_status == "failed":
        raise RuntimeError(
            "Failed to generate PDF and could not write print-HTML fallback "
            f"({result.fallback_reason}; {result.error_type})."
        )
    return result.generated_pdf


def _inject_auto_print_script(html_content: str) -> str:
    """Injects print media CSS and auto-print trigger for browser fallback."""
    print_styles = """
    <style media="print">
      @page { size: A4; margin: 20mm; }
      body { font-family: Georgia, 'Times New Roman', serif; background: #fff !important; color: #000 !important; }
      .no-print, nav, button, .action-buttons { display: none !important; }
      .page-break { page-break-after: always; }
    </style>
    """
    if "</head>" in html_content:
        return html_content.replace("</head>", f"{print_styles}\n</head>")
    return f"{print_styles}\n{html_content}"
