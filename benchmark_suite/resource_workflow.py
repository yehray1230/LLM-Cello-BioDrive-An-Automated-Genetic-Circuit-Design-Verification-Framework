from __future__ import annotations

from typing import Any, Mapping

from benchmark_suite.resource_parameter_fitting import (
    ResourceFitObservation,
    fit_resource_competition_parameters,
    observations_from_derived_metrics,
)
from benchmark_suite.resource_plate_reader import (
    PlateReaderPreprocessingConfig,
    load_plate_map,
    preprocess_plate_reader_csv,
)
from benchmark_suite.resource_validation import validate_resource_profile_heldout
from schemas.resource_calibration import (
    calibration_context_from_dict,
    construct_metadata_from_dict,
    validation_split_from_dict,
)


RESOURCE_WORKFLOW_VERSION = "resource_calibration_workflow_v0.1"
CLAIM_BOUNDARY = (
    "This research-preview workflow reports context-bound computational calibration "
    "evidence. It does not establish wet-lab validity and never updates production "
    "simulation parameters automatically."
)


def run_resource_calibration_workflow(request: Mapping[str, Any]) -> dict[str, Any]:
    """Run M2-M4 as one traceable, claim-safe diagnostics workflow."""
    input_mode = str(request.get("input_mode") or "")
    if input_mode not in {"raw_plate_reader", "derived_observations"}:
        raise ValueError(
            "input_mode must be 'raw_plate_reader' or 'derived_observations'."
        )
    dataset_id = _required_text(request, "dataset_id")
    validation_dataset_id = _required_text(request, "validation_dataset_id")
    context_payload = _required_mapping(request, "context")
    construct_payloads = request.get("constructs")
    if not isinstance(construct_payloads, list) or not construct_payloads:
        raise ValueError("constructs must be a non-empty list.")
    split_payload = _required_mapping(request, "validation_split")

    context = calibration_context_from_dict(dict(context_payload))
    constructs = tuple(
        construct_metadata_from_dict(dict(item)) for item in construct_payloads
    )
    split = validation_split_from_dict(dict(split_payload))
    known_construct_ids = {item.construct_id for item in constructs}
    split.validate_constructs(known_construct_ids)
    stages: dict[str, dict[str, Any]] = {
        "context_review": {
            "status": "completed",
            "context_id": context.context_id,
            "context_fingerprint": context.fingerprint(),
            "construct_count": len(constructs),
            "split_id": split.split_id,
        }
    }
    warnings: list[str] = []
    preprocessing: dict[str, Any]

    if input_mode == "raw_plate_reader":
        raw_csv = str(request.get("raw_csv") or "")
        if not raw_csv.strip():
            raise ValueError("raw_csv is required for raw_plate_reader input.")
        plate_map_payload = request.get("plate_map")
        if not isinstance(plate_map_payload, list) or not plate_map_payload:
            raise ValueError("plate_map is required for raw_plate_reader input.")
        baseline_construct_id = _required_text(request, "baseline_construct_id")
        config_payload = request.get("preprocessing_config") or {}
        if not isinstance(config_payload, Mapping):
            raise ValueError("preprocessing_config must be an object.")
        config = PlateReaderPreprocessingConfig(**dict(config_payload))
        preprocessing_report = preprocess_plate_reader_csv(
            raw_csv,
            context=context,
            constructs=constructs,
            plate_map=load_plate_map(plate_map_payload),
            config=config,
        )
        preprocessing = preprocessing_report.to_dict()
        stages["preprocessing"] = {
            "status": preprocessing_report.status,
            "record_count": len(preprocessing_report.records),
            "derived_metric_count": len(preprocessing_report.derived_metrics),
            "excluded_well_count": len(preprocessing_report.excluded_wells),
        }
        warnings.extend(preprocessing_report.warnings)
        if preprocessing_report.status == "failed_qc":
            return _blocked_report(
                input_mode=input_mode,
                dataset_id=dataset_id,
                validation_dataset_id=validation_dataset_id,
                context=context.to_dict(),
                constructs=[item.to_dict() for item in constructs],
                split=split.to_dict(),
                stages=stages,
                preprocessing=preprocessing,
                observations=(),
                warnings=warnings,
                dominant_layer="preprocessing_qc",
                detail="No plate-reader records passed preprocessing QC.",
            )
        try:
            observations = observations_from_derived_metrics(
                preprocessing_report.derived_metrics,
                demand_index_by_construct=_float_map(
                    request.get("demand_index_by_construct")
                ),
                demand_index_by_condition=_float_map(
                    request.get("demand_index_by_condition")
                ),
                baseline_construct_id=baseline_construct_id,
            )
        except ValueError as exc:
            stages["observation_derivation"] = {
                "status": "blocked",
                "detail": str(exc),
            }
            return _blocked_report(
                input_mode=input_mode,
                dataset_id=dataset_id,
                validation_dataset_id=validation_dataset_id,
                context=context.to_dict(),
                constructs=[item.to_dict() for item in constructs],
                split=split.to_dict(),
                stages=stages,
                preprocessing=preprocessing,
                observations=(),
                warnings=warnings,
                dominant_layer="observation_derivation",
                detail=str(exc),
            )
        stages["observation_derivation"] = {
            "status": "completed",
            "observation_count": len(observations),
        }
    else:
        observation_payloads = request.get("observations")
        if not isinstance(observation_payloads, list) or not observation_payloads:
            raise ValueError(
                "observations are required for derived_observations input."
            )
        observations = tuple(
            _observation_from_dict(dict(item)) for item in observation_payloads
        )
        preprocessing = {
            "status": "not_run_prederived_input",
            "warnings": [
                "Raw plate-reader QC was not run because governed derived "
                "observations were supplied."
            ],
        }
        stages["preprocessing"] = {"status": "not_run_prederived_input"}
        stages["observation_derivation"] = {
            "status": "provided",
            "observation_count": len(observations),
        }
        warnings.extend(preprocessing["warnings"])

    training_ids = set(split.training_construct_ids)
    validation_ids = set(split.validation_construct_ids)
    training = tuple(
        item for item in observations if item.construct_id in training_ids
    )
    validation = tuple(
        item for item in observations if item.construct_id in validation_ids
    )
    stages["partition"] = {
        "status": "completed" if training and validation else "blocked",
        "training_observation_count": len(training),
        "validation_observation_count": len(validation),
    }
    if not training or not validation:
        return _blocked_report(
            input_mode=input_mode,
            dataset_id=dataset_id,
            validation_dataset_id=validation_dataset_id,
            context=context.to_dict(),
            constructs=[item.to_dict() for item in constructs],
            split=split.to_dict(),
            stages=stages,
            preprocessing=preprocessing,
            observations=observations,
            warnings=warnings,
            dominant_layer="frozen_partition",
            detail="Frozen split produced an empty training or validation partition.",
        )

    try:
        fit = fit_resource_competition_parameters(
            training,
            context_id=context.context_id,
            dataset_id=dataset_id,
            defaults=_float_map(request.get("defaults")) or None,
            bounds=_bounds_map(request.get("bounds")) or None,
            bootstrap_samples=int(request.get("bootstrap_samples", 100)),
            bootstrap_seed=int(request.get("bootstrap_seed", 1729)),
        )
    except (RuntimeError, ValueError) as exc:
        stages["fitting"] = {"status": "blocked", "detail": str(exc)}
        return _blocked_report(
            input_mode=input_mode,
            dataset_id=dataset_id,
            validation_dataset_id=validation_dataset_id,
            context=context.to_dict(),
            constructs=[item.to_dict() for item in constructs],
            split=split.to_dict(),
            stages=stages,
            preprocessing=preprocessing,
            observations=observations,
            warnings=warnings,
            dominant_layer="parameter_fitting",
            detail=str(exc),
        )
    profile = fit.profile
    stages["fitting"] = {
        "status": profile.diagnostics.status,
        "profile_id": profile.profile_id,
        "usable_for_prediction": profile.usable_for_prediction,
        "reason_codes": list(profile.diagnostics.reason_codes),
    }
    warnings.extend(profile.warnings)

    try:
        validation_report = validate_resource_profile_heldout(
            profile=profile,
            split=split,
            training_observations=training,
            validation_observations=validation,
            validation_context_id=context.context_id,
            validation_dataset_id=validation_dataset_id,
            observed_output_fold_changes=_float_map(
                request.get("observed_output_fold_changes")
            )
            or None,
            predicted_output_fold_changes=_float_map(
                request.get("predicted_output_fold_changes")
            )
            or None,
            output_prediction_model_id=str(
                request.get("output_prediction_model_id") or ""
            ),
        )
    except ValueError as exc:
        stages["heldout_validation"] = {"status": "blocked", "detail": str(exc)}
        return _blocked_report(
            input_mode=input_mode,
            dataset_id=dataset_id,
            validation_dataset_id=validation_dataset_id,
            context=context.to_dict(),
            constructs=[item.to_dict() for item in constructs],
            split=split.to_dict(),
            stages=stages,
            preprocessing=preprocessing,
            observations=observations,
            warnings=warnings,
            dominant_layer="heldout_validation_contract",
            detail=str(exc),
            fit=fit.to_dict(),
        )

    validation_dict = validation_report.to_dict()
    stages["heldout_validation"] = {
        "status": validation_report.decision,
        "report_id": validation_report.report_id,
        "claim_state": validation_report.claim_state,
        "failed_gates": [
            gate.gate_name for gate in validation_report.gates if not gate.passed
        ],
    }
    warnings.extend(validation_report.warnings)
    failed_gates = stages["heldout_validation"]["failed_gates"]
    dominant_layer = (
        "parameter_identifiability"
        if not profile.usable_for_prediction
        else str(failed_gates[0])
        if failed_gates
        else "heldout_gates_passed"
    )
    decision = validation_report.decision
    return {
        "workflow_version": RESOURCE_WORKFLOW_VERSION,
        "status": "completed" if decision == "go" else "completed_with_limits",
        "input_mode": input_mode,
        "dataset_id": dataset_id,
        "validation_dataset_id": validation_dataset_id,
        "context": context.to_dict(),
        "constructs": [item.to_dict() for item in constructs],
        "validation_split": split.to_dict(),
        "stages": stages,
        "preprocessing": preprocessing,
        "observations": [item.to_dict() for item in observations],
        "fit": fit.to_dict(),
        "validation": validation_dict,
        "candidate_comparison": validation_dict["predictions"],
        "dominant_layer": dominant_layer,
        "parameter_role_summary": {
            "observed": len(observations) * 2,
            "calibrated": len(profile.parameters) if profile.usable_for_prediction else 0,
            "defaulted": len(profile.fixed_assumptions),
            "inferred": len(fit.fitted_points) + len(validation_report.predictions),
        },
        "provenance": _provenance_summary(
            input_mode, context.fingerprint(), observations, preprocessing
        ),
        "warnings": _deduplicate(warnings),
        "claim_boundary": {
            "claim_state": validation_report.claim_state,
            "decision": decision,
            "statement": CLAIM_BOUNDARY,
        },
        "automatic_application": {
            "allowed": False,
            "reason": "Manual review and an explicit future promotion step are required.",
        },
    }


def _blocked_report(
    *,
    input_mode: str,
    dataset_id: str,
    validation_dataset_id: str,
    context: dict[str, Any],
    constructs: list[dict[str, Any]],
    split: dict[str, Any],
    stages: dict[str, dict[str, Any]],
    preprocessing: dict[str, Any],
    observations: tuple[ResourceFitObservation, ...],
    warnings: list[str],
    dominant_layer: str,
    detail: str,
    fit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    warning_list = _deduplicate([*warnings, detail])
    return {
        "workflow_version": RESOURCE_WORKFLOW_VERSION,
        "status": "blocked",
        "input_mode": input_mode,
        "dataset_id": dataset_id,
        "validation_dataset_id": validation_dataset_id,
        "context": context,
        "constructs": constructs,
        "validation_split": split,
        "stages": stages,
        "preprocessing": preprocessing,
        "observations": [item.to_dict() for item in observations],
        "fit": fit,
        "validation": None,
        "candidate_comparison": [],
        "dominant_layer": dominant_layer,
        "parameter_role_summary": {
            "observed": len(observations) * 2,
            "calibrated": 0,
            "defaulted": 4,
            "inferred": 0,
        },
        "provenance": _provenance_summary(
            input_mode,
            str(stages["context_review"]["context_fingerprint"]),
            observations,
            preprocessing,
        ),
        "warnings": warning_list,
        "claim_boundary": {
            "claim_state": "insufficient_evidence_no_go",
            "decision": "no_go",
            "statement": CLAIM_BOUNDARY,
        },
        "automatic_application": {
            "allowed": False,
            "reason": "Workflow is blocked and cannot produce a promotable profile.",
        },
    }


def _observation_from_dict(payload: dict[str, Any]) -> ResourceFitObservation:
    payload["source_metric_ids"] = tuple(payload.get("source_metric_ids") or ())
    return ResourceFitObservation(**payload)


def _provenance_summary(
    input_mode: str,
    context_fingerprint: str,
    observations: tuple[ResourceFitObservation, ...],
    preprocessing: dict[str, Any],
) -> dict[str, Any]:
    metric_ids = sorted(
        {metric_id for item in observations for metric_id in item.source_metric_ids}
    )
    return {
        "input_mode": input_mode,
        "context_fingerprint": context_fingerprint,
        "observation_ids": sorted(item.observation_id for item in observations),
        "source_metric_ids": metric_ids,
        "plate_map_fingerprint": preprocessing.get("plate_map_fingerprint"),
        "raw_trace_count": len(preprocessing.get("trace_provenance") or {}),
    }


def _required_text(request: Mapping[str, Any], name: str) -> str:
    value = str(request.get(name) or "").strip()
    if not value:
        raise ValueError(f"{name} must be non-empty.")
    return value


def _required_mapping(request: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = request.get(name)
    if not isinstance(value, Mapping) or not value:
        raise ValueError(f"{name} must be a non-empty object.")
    return value


def _float_map(value: Any) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("Expected an object mapping identifiers to numeric values.")
    return {str(key): float(item) for key, item in value.items()}


def _bounds_map(value: Any) -> dict[str, tuple[float, float]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("bounds must be an object.")
    result = {}
    for name, pair in value.items():
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError(f"Bound for {name!r} must contain two values.")
        result[str(name)] = (float(pair[0]), float(pair[1]))
    return result


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))
