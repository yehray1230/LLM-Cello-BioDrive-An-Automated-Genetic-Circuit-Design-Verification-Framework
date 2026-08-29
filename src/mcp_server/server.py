from __future__ import annotations

from typing import Any

from mcp_server.service import (
    cancel_design_run as service_cancel_design_run,
    compare_design_runs as service_compare_design_runs,
    compare_design_revisions as service_compare_design_revisions,
    design_circuit_quick,
    diagnose_design_run as service_diagnose_design_run,
    evaluate_verilog,
    explain_design_run as service_explain_design_run,
    export_design as service_export_design,
    get_design_ir as service_get_design_ir,
    get_design_run_artifacts as service_get_design_run_artifacts,
    get_design_run_result as service_get_design_run_result,
    get_design_run_status as service_get_design_run_status,
    get_design_run_events as service_get_design_run_events,
    get_design_run_progress as service_get_design_run_progress,
    list_compatible_replacements as service_list_compatible_replacements,
    list_design_runs as service_list_design_runs,
    list_tool_capabilities as service_list_tool_capabilities,
    start_design_run as service_start_design_run,
    submit_design_feedback as service_submit_design_feedback,
    summarize_design_state,
    replace_design_part as service_replace_design_part,
    resume_design_run as service_resume_design_run,
    validate_design_part_replacement as service_validate_design_part_replacement,
)


try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional runtime package
    FastMCP = None  # type: ignore[assignment]
    MCP_IMPORT_ERROR = exc
else:
    MCP_IMPORT_ERROR = None


if FastMCP is not None:
    mcp = FastMCP("genetic-circuit-workflow")

    @mcp.tool()
    def design_genetic_circuit_quick(
        user_intent: str,
        host_organism: str = "Escherichia coli",
        compute_budget: int = 2,
        enable_rag: bool = True,
        enable_ode: bool = True,
        enable_skill_extraction: bool = True,
        monte_carlo_samples: int = 1,
        model_name: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        output_dir: str | None = None,
        cello_command: str | None = None,
        ucf_path: str | None = None,
        sensor_path: str | None = None,
        device_path: str | None = None,
        cello_artifact_format: str = "cello_v2",
    ) -> dict[str, Any]:
        """Run a compact genetic circuit design workflow and write artifacts."""
        return design_circuit_quick(
            user_intent=user_intent,
            host_organism=host_organism,
            compute_budget=compute_budget,
            enable_rag=enable_rag,
            enable_ode=enable_ode,
            enable_skill_extraction=enable_skill_extraction,
            monte_carlo_samples=monte_carlo_samples,
            model_name=model_name,
            api_base=api_base,
            api_key=api_key,
            output_dir=output_dir,
            cello_command=cello_command,
            cello_artifact_format=cello_artifact_format,
            ucf_path=ucf_path,
            sensor_path=sensor_path,
            device_path=device_path,
        )

    @mcp.tool()
    def evaluate_cello_verilog(
        verilog: str,
        user_intent: str = "Evaluate a Cello-compatible genetic circuit.",
        host_organism: str = "Escherichia coli",
        enable_ode: bool = True,
        monte_carlo_samples: int = 1,
        output_dir: str | None = None,
        cello_command: str | None = None,
        ucf_path: str | None = None,
        sensor_path: str | None = None,
        device_path: str | None = None,
        cello_artifact_format: str = "cello_v2",
    ) -> dict[str, Any]:
        """Evaluate existing Cello-compatible Verilog without calling an LLM."""
        return evaluate_verilog(
            verilog=verilog,
            user_intent=user_intent,
            host_organism=host_organism,
            enable_ode=enable_ode,
            monte_carlo_samples=monte_carlo_samples,
            output_dir=output_dir,
            cello_command=cello_command,
            cello_artifact_format=cello_artifact_format,
            ucf_path=ucf_path,
            sensor_path=sensor_path,
            device_path=device_path,
        )

    @mcp.tool()
    def start_design_run(
        user_intent: str,
        host_organism: str = "Escherichia coli",
        compute_budget: int = 6,
        enable_rag: bool = True,
        enable_ode: bool = True,
        enable_skill_extraction: bool = True,
        monte_carlo_samples: int = 1,
        model_name: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        output_dir: str | None = None,
        cello_command: str | None = None,
        ucf_path: str | None = None,
        sensor_path: str | None = None,
        device_path: str | None = None,
        cello_artifact_format: str = "cello_v2",
    ) -> dict[str, Any]:
        """Start a background genetic circuit design run and return a run_id."""
        return service_start_design_run(
            user_intent=user_intent,
            host_organism=host_organism,
            compute_budget=compute_budget,
            enable_rag=enable_rag,
            enable_ode=enable_ode,
            enable_skill_extraction=enable_skill_extraction,
            monte_carlo_samples=monte_carlo_samples,
            model_name=model_name,
            api_base=api_base,
            api_key=api_key,
            output_dir=output_dir,
            cello_command=cello_command,
            cello_artifact_format=cello_artifact_format,
            ucf_path=ucf_path,
            sensor_path=sensor_path,
            device_path=device_path,
        )

    @mcp.tool()
    def get_design_run_status(run_id: str) -> dict[str, Any]:
        """Return queued/running/completed status for a background design run."""
        return service_get_design_run_status(run_id)

    @mcp.tool()
    def get_design_run_events(
        run_id: str,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return persisted stage events for a run."""
        return service_get_design_run_events(run_id, after_event_id, limit)

    @mcp.tool()
    def get_design_run_progress(run_id: str) -> dict[str, Any]:
        """Return the current stage, progress fraction, and event count."""
        return service_get_design_run_progress(run_id)

    @mcp.tool()
    def get_design_run_result(run_id: str) -> dict[str, Any]:
        """Return the final result and artifacts for a background design run."""
        return service_get_design_run_result(run_id)

    @mcp.tool()
    def list_design_runs(limit: int = 20) -> dict[str, Any]:
        """List recent background design runs, newest first."""
        return service_list_design_runs(limit=limit)

    @mcp.tool()
    def list_tool_capabilities() -> dict[str, Any]:
        """List optional biological tool capabilities, availability, and fallbacks."""
        return service_list_tool_capabilities()

    @mcp.tool()
    def cancel_design_run(run_id: str) -> dict[str, Any]:
        """Best-effort cancellation for a queued or running background design run."""
        return service_cancel_design_run(run_id)

    @mcp.tool()
    def get_design_run_artifacts(run_id: str) -> dict[str, Any]:
        """Return artifact paths and manifest details for a background design run."""
        return service_get_design_run_artifacts(run_id)

    @mcp.tool()
    def compare_design_runs(run_ids: list[str]) -> dict[str, Any]:
        """Compare completed design runs and rank them by design metrics."""
        return service_compare_design_runs(run_ids)

    @mcp.tool()
    def diagnose_design_run(run_id: str) -> dict[str, Any]:
        """Diagnose a design run using deterministic workflow and benchmark signals."""
        return service_diagnose_design_run(run_id)

    @mcp.tool()
    def explain_design_run(
        run_id: str,
        profile: str = "review",
        sections: list[str] | None = None,
        max_items_per_section: int = 3,
        include_raw_metrics: bool = False,
        include_verilog: bool = False,
        write_artifacts: bool = True,
    ) -> dict[str, Any]:
        """Return selectable score and decision-rationale explanations for a completed run."""
        return service_explain_design_run(
            run_id=run_id,
            profile=profile,
            sections=sections,
            max_items_per_section=max_items_per_section,
            include_raw_metrics=include_raw_metrics,
            include_verilog=include_verilog,
            write_artifacts=write_artifacts,
        )

    @mcp.tool()
    def submit_design_feedback(
        run_id: str,
        constraints: list[str],
        action: str = "repair",
        extra_budget: int = 2,
    ) -> dict[str, Any]:
        """Persist human constraints and a repair, exploitation, or fallback choice."""
        return service_submit_design_feedback(run_id, constraints, action, extra_budget)

    @mcp.tool()
    def resume_design_run(
        run_id: str,
        model_name: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        cello_artifact_format: str | None = None,
    ) -> dict[str, Any]:
        """Create a child run that resumes a paused design from saved state."""
        return service_resume_design_run(
            run_id,
            model_name,
            api_base,
            api_key,
            cello_artifact_format=cello_artifact_format,
        )

    @mcp.tool()
    def get_design_ir(run_id: str, revision_id: str | None = None) -> dict[str, Any]:
        """Return or materialize the canonical DesignIR for a run revision."""
        return service_get_design_ir(run_id, revision_id)

    @mcp.tool()
    def list_compatible_replacements(
        run_id: str,
        target_part_id: str,
        revision_id: str | None = None,
        library_path: str | None = None,
    ) -> dict[str, Any]:
        """List library parts compatible with one DesignIR part."""
        return service_list_compatible_replacements(
            run_id, target_part_id, revision_id, library_path
        )

    @mcp.tool()
    def validate_design_part_replacement(
        run_id: str,
        target_part_id: str,
        replacement_part_id: str,
        revision_id: str | None = None,
        library_path: str | None = None,
    ) -> dict[str, Any]:
        """Validate a proposed immutable part replacement."""
        return service_validate_design_part_replacement(
            run_id, target_part_id, replacement_part_id, revision_id, library_path
        )

    @mcp.tool()
    def replace_design_part(
        run_id: str,
        target_part_id: str,
        replacement_part_id: str,
        revision_id: str | None = None,
        library_path: str | None = None,
        created_by: str = "mcp_user",
    ) -> dict[str, Any]:
        """Create and persist a new DesignIR revision with one part replaced."""
        return service_replace_design_part(
            run_id,
            target_part_id,
            replacement_part_id,
            revision_id,
            library_path,
            created_by,
        )

    @mcp.tool()
    def compare_design_revisions(
        run_id: str,
        left_revision_id: str,
        right_revision_id: str,
    ) -> dict[str, Any]:
        """Compare two persisted DesignIR revisions."""
        return service_compare_design_revisions(run_id, left_revision_id, right_revision_id)

    @mcp.tool()
    def export_design(
        run_id: str,
        revision_id: str | None = None,
        formats: list[str] | None = None,
    ) -> dict[str, Any]:
        """Export a DesignIR revision as BOM CSV, GenBank, and/or SBOL3."""
        return service_export_design(run_id, revision_id, formats)

    @mcp.tool()
    def summarize_mcp_design_state(state_json: dict[str, Any]) -> dict[str, Any]:
        """Summarize a state JSON previously produced by this MCP adapter."""
        return summarize_design_state(state_json)


def main() -> None:
    if FastMCP is None:
        raise SystemExit(
            "The optional 'mcp' package is not installed. Install it with `pip install mcp` "
            "or add `mcp>=1.0` to your environment before running this server."
        ) from MCP_IMPORT_ERROR
    mcp.run()


if __name__ == "__main__":
    main()
