from __future__ import annotations

from typing import Any

try:
    import primer3 as _primer3
except ModuleNotFoundError:
    _primer3 = None
from Bio.Seq import Seq

from schemas.assembly_deliverables import (
    FragmentPrimerSet,
    Primer,
    PrimerDesignResult,
    PrimerWarning,
)
from utils.package_metadata import package_version as _package_version


MIN_PCR_TEMPLATE_LENGTH = 60
SECONDARY_STRUCTURE_WARNING_TM = 45.0


def _primer3_backend() -> Any:
    if _primer3 is None:
        raise RuntimeError(
            "Primer design requires the optional GPL-2.0 dependency "
            "'primer3-py'. Install the 'primer-design' extra only after "
            "reviewing its redistribution terms."
        )
    return _primer3


def design_assembly_primers(
    assembly_plan: dict[str, Any],
    *,
    primer_min_size: int = 18,
    primer_opt_size: int = 20,
    primer_max_size: int = 28,
    primer_min_tm: float = 57.0,
    primer_opt_tm: float = 60.0,
    primer_max_tm: float = 63.0,
    primer_min_gc: float = 35.0,
    primer_max_gc: float = 65.0,
) -> PrimerDesignResult:
    fragment_sets: list[FragmentPrimerSet] = []
    primer3_backend = _primer3_backend()
    result_warnings: list[PrimerWarning] = []
    for fragment in assembly_plan.get("fragments") or []:
        core = _dna(fragment.get("core_sequence"))
        left_adapter = _dna(fragment.get("left_adapter"))
        right_adapter = _dna(fragment.get("right_adapter"))
        fragment_id = str(fragment.get("fragment_id") or "fragment")
        fragment_name = str(fragment.get("name") or fragment_id)
        source_type = str(fragment.get("source_type") or "unknown")
        if len(core) < MIN_PCR_TEMPLATE_LENGTH:
            warning = PrimerWarning(
                code="DIRECT_SYNTHESIS_RECOMMENDED",
                message=(
                    f"{fragment_name} is {len(core)} bp; direct synthesis is "
                    "recommended instead of PCR primer design."
                ),
                severity="info",
            )
            fragment_sets.append(
                FragmentPrimerSet(
                    fragment_id=fragment_id,
                    fragment_name=fragment_name,
                    source_type=source_type,
                    preparation="direct_synthesis",
                    template_length=len(core),
                    product_length=len(core) + len(left_adapter) + len(right_adapter),
                    warnings=[warning],
                )
            )
            continue

        primer_payload = primer3_backend.bindings.design_primers(
            {
                "SEQUENCE_ID": fragment_id,
                "SEQUENCE_TEMPLATE": core,
            },
            {
                "PRIMER_TASK": "generic",
                "PRIMER_PICK_LEFT_PRIMER": 1,
                "PRIMER_PICK_RIGHT_PRIMER": 1,
                "PRIMER_NUM_RETURN": 1,
                "PRIMER_MIN_SIZE": primer_min_size,
                "PRIMER_OPT_SIZE": primer_opt_size,
                "PRIMER_MAX_SIZE": primer_max_size,
                "PRIMER_MIN_TM": primer_min_tm,
                "PRIMER_OPT_TM": primer_opt_tm,
                "PRIMER_MAX_TM": primer_max_tm,
                "PRIMER_MIN_GC": primer_min_gc,
                "PRIMER_MAX_GC": primer_max_gc,
                "PRIMER_PRODUCT_SIZE_RANGE": [[min(40, len(core)), len(core)]],
            },
        )
        if int(primer_payload.get("PRIMER_PAIR_NUM_RETURNED", 0)) < 1:
            warning = PrimerWarning(
                code="PRIMER3_NO_PAIR",
                message=(
                    f"primer3 could not design a primer pair for {fragment_name}: "
                    f"{primer_payload.get('PRIMER_PAIR_EXPLAIN', 'no explanation')}"
                ),
                severity="error",
            )
            result_warnings.append(warning)
            fragment_sets.append(
                FragmentPrimerSet(
                    fragment_id=fragment_id,
                    fragment_name=fragment_name,
                    source_type=source_type,
                    preparation="blocked",
                    template_length=len(core),
                    product_length=len(core) + len(left_adapter) + len(right_adapter),
                    warnings=[warning],
                )
            )
            continue

        forward_binding = _dna(primer_payload["PRIMER_LEFT_0_SEQUENCE"])
        reverse_binding = _dna(primer_payload["PRIMER_RIGHT_0_SEQUENCE"])
        forward = _primer(
            fragment_id,
            "forward",
            left_adapter,
            forward_binding,
            float(primer_payload["PRIMER_LEFT_0_TM"]),
            float(primer_payload["PRIMER_LEFT_0_GC_PERCENT"]),
            primer_min_size,
            primer_max_size,
            primer_min_tm,
            primer_max_tm,
            primer_min_gc,
            primer_max_gc,
        )
        reverse = _primer(
            fragment_id,
            "reverse",
            str(Seq(right_adapter).reverse_complement()),
            reverse_binding,
            float(primer_payload["PRIMER_RIGHT_0_TM"]),
            float(primer_payload["PRIMER_RIGHT_0_GC_PERCENT"]),
            primer_min_size,
            primer_max_size,
            primer_min_tm,
            primer_max_tm,
            primer_min_gc,
            primer_max_gc,
        )
        heterodimer = primer3_backend.bindings.calc_heterodimer(
            forward.sequence,
            reverse.sequence,
        )
        pair_warnings: list[PrimerWarning] = []
        if heterodimer.structure_found and heterodimer.tm >= SECONDARY_STRUCTURE_WARNING_TM:
            pair_warnings.append(
                PrimerWarning(
                    code="PRIMER_HETERODIMER",
                    message=(
                        f"{fragment_name} primer pair has predicted heterodimer "
                        f"Tm {heterodimer.tm:.1f} C."
                    ),
                )
            )
        fragment_sets.append(
            FragmentPrimerSet(
                fragment_id=fragment_id,
                fragment_name=fragment_name,
                source_type=source_type,
                preparation="pcr",
                template_length=len(core),
                product_length=len(core) + len(left_adapter) + len(right_adapter),
                forward_primer=forward,
                reverse_primer=reverse,
                heterodimer_tm=round(float(heterodimer.tm), 2),
                warnings=pair_warnings,
            )
        )

    status = (
        "blocked"
        if any(item.preparation == "blocked" for item in fragment_sets)
        else "ready"
    )
    return PrimerDesignResult(
        status=status,
        fragment_primer_sets=fragment_sets,
        warnings=result_warnings,
        tool_versions={"primer3-py": _package_version("primer3-py")},
    )


def _primer(
    fragment_id: str,
    direction: str,
    adapter: str,
    binding: str,
    tm: float,
    gc_percent: float,
    min_size: int,
    max_size: int,
    min_tm: float,
    max_tm: float,
    min_gc: float,
    max_gc: float,
) -> Primer:
    sequence = adapter + binding
    warnings: list[PrimerWarning] = []
    primer3_backend = _primer3_backend()
    if not min_size <= len(binding) <= max_size:
        warnings.append(_warning("PRIMER_LENGTH", len(binding), min_size, max_size))
    if not min_tm <= tm <= max_tm:
        warnings.append(_warning("PRIMER_TM", tm, min_tm, max_tm))
    if not min_gc <= gc_percent <= max_gc:
        warnings.append(_warning("PRIMER_GC", gc_percent, min_gc, max_gc))
    hairpin = primer3_backend.bindings.calc_hairpin(sequence)
    homodimer = primer3_backend.bindings.calc_homodimer(sequence)
    if hairpin.structure_found and hairpin.tm >= SECONDARY_STRUCTURE_WARNING_TM:
        warnings.append(
            PrimerWarning(
                code="PRIMER_HAIRPIN",
                message=f"Predicted hairpin Tm is {hairpin.tm:.1f} C.",
            )
        )
    if homodimer.structure_found and homodimer.tm >= SECONDARY_STRUCTURE_WARNING_TM:
        warnings.append(
            PrimerWarning(
                code="PRIMER_HOMODIMER",
                message=f"Predicted homodimer Tm is {homodimer.tm:.1f} C.",
            )
        )
    return Primer(
        primer_id=f"{fragment_id}_{direction}",
        name=f"{fragment_id} {direction}",
        direction=direction,
        sequence=sequence,
        annealing_sequence=binding,
        adapter_sequence=adapter,
        length=len(sequence),
        annealing_length=len(binding),
        tm=round(tm, 2),
        gc_percent=round(gc_percent, 2),
        hairpin_tm=round(float(hairpin.tm), 2),
        homodimer_tm=round(float(homodimer.tm), 2),
        warnings=warnings,
    )


def _warning(code: str, value: float, minimum: float, maximum: float) -> PrimerWarning:
    return PrimerWarning(
        code=code,
        message=f"Value {value:.1f} is outside the configured {minimum:.1f}-{maximum:.1f} range.",
    )


def _dna(value: Any) -> str:
    return str(value or "").strip().upper()
