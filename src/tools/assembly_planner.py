from __future__ import annotations

from io import StringIO
from itertools import pairwise
import re
from uuid import uuid4

from Bio import Restriction, SeqIO
from Bio.Seq import Seq
from Bio.SeqUtils import MeltingTemp as mt
try:
    import pydivsufsort  # noqa: F401
except ImportError:
    import sys
    import types

    def _pure_python_common_substrings(stringx, stringy, limit=15):
        sx, sy = str(stringx), str(stringy)
        len_x, len_y = len(sx), len(sy)
        matches = []
        seen = set()
        for i in range(len_x - limit + 1):
            sub = sx[i : i + limit]
            pos = sy.find(sub)
            while pos != -1:
                k = limit
                while i + k < len_x and pos + k < len_y and sx[i + k] == sy[pos + k]:
                    k += 1
                if i == 0 or pos == 0 or sx[i - 1] != sy[pos - 1]:
                    item = (i, pos, k)
                    if item not in seen:
                        seen.add(item)
                        matches.append(item)
                pos = sy.find(sub, pos + 1)
        return matches

    _fake = types.ModuleType("pydivsufsort")
    _fake.common_substrings = _pure_python_common_substrings
    sys.modules["pydivsufsort"] = _fake

from pydna.assembly2 import Assembly, golden_gate_assembly
from pydna.dseqrecord import Dseqrecord

from exporters.plasmid_tools import PlasmidAssemblyResult
from schemas.assembly_plan import (
    AssemblyFragment,
    AssemblyJunction,
    AssemblyPlan,
    AssemblyScar,
    PlanIssue,
    RestrictionDigest,
)
from schemas.backbone_registry import BackboneRegistryEntry
from utils.package_metadata import package_version as _package_version


TYPE_IIS_ENZYMES = {"BsaI", "BsmBI"}
DEFAULT_GOLDEN_GATE_OVERHANGS = (
    "AATG",
    "AGGT",
    "GCTT",
    "CGCT",
    "TGCC",
    "ACTC",
    "GGAG",
    "TACA",
    "CAGG",
    "GTCA",
    "ATCC",
    "CTGA",
)


def create_assembly_plan(
    assembly: PlasmidAssemblyResult,
    backbone: BackboneRegistryEntry,
    *,
    method: str,
    insertion_start: int,
    insertion_end: int,
    restriction_enzymes: list[str] | None = None,
    gibson_overlap_length: int = 25,
    golden_gate_enzyme: str = "BsaI",
    golden_gate_overhangs: list[str] | None = None,
) -> AssemblyPlan:
    if not assembly.ok:
        raise ValueError("Cannot plan assembly for a blocked plasmid assembly.")
    method = str(method).lower()
    if method not in {"restriction_cloning", "gibson", "golden_gate"}:
        raise ValueError(f"Unsupported assembly planning method: {method}")
    target = SeqIO.read(StringIO(assembly.genbank), "genbank")
    backbone_record = SeqIO.read(StringIO(backbone.genbank), "genbank")
    insert_length = assembly.report.insert_length
    insert_sequence = str(
        target.seq[insertion_start : insertion_start + insert_length]
    ).upper()
    backbone_core = (
        str(backbone_record.seq[insertion_end:])
        + str(backbone_record.seq[:insertion_start])
    ).upper()
    plan = AssemblyPlan(
        plan_id=f"assembly_plan_{uuid4().hex[:12]}",
        design_id=assembly.report.design_id,
        plasmid_id=assembly.report.plasmid_id,
        method=method,
        status="planning",
        backbone_id=backbone.backbone_id,
        backbone_version=backbone.version,
        insertion_region_id=assembly.report.insertion_region_id or "",
        target_length=len(target.seq),
        target_checksum=assembly.report.sequence_checksum,
        tool_versions={
            "biopython": _package_version("biopython"),
            "pydna": _package_version("pydna"),
        },
    )
    enzymes = restriction_enzymes or ["EcoRI", "BsaI", "BsmBI"]
    plan.digests.extend(
        analyze_restriction_digest(
            "backbone",
            str(backbone_record.seq),
            enzymes,
            circular=True,
            issues=plan.issues,
        )
    )
    plan.digests.extend(
        analyze_restriction_digest(
            "insert",
            insert_sequence,
            enzymes,
            circular=False,
            issues=plan.issues,
        )
    )
    plan.digests.extend(
        analyze_restriction_digest(
            "target_plasmid",
            str(target.seq),
            enzymes,
            circular=True,
            issues=plan.issues,
        )
    )
    if method == "gibson":
        _plan_gibson(
            plan,
            backbone_core,
            insert_sequence,
            overlap_length=gibson_overlap_length,
        )
    elif method == "golden_gate":
        _plan_golden_gate(
            plan,
            backbone_core,
            insert_sequence,
            enzyme_name=golden_gate_enzyme,
            requested_overhangs=golden_gate_overhangs,
        )
        enzyme = _restriction_enzyme(golden_gate_enzyme)
        if enzyme is not None:
            sites = enzyme.search(target.seq, linear=False)
            if sites:
                plan.issues.append(
                    PlanIssue(
                        code="GOLDEN_GATE_RETAINED_SITE",
                        message=(
                            f"Assembled plasmid product contains {len(sites)} remaining/re-created "
                            f"{golden_gate_enzyme} recognition site(s) at positions: "
                            f"{', '.join(str(val) for val in sites)}. This will lead to re-cutting during assembly."
                        ),
                        severity="error",
                    )
                )
    else:
        _plan_restriction_cloning(
            plan,
            backbone_core,
            insert_sequence,
            enzymes=enzymes,
        )
    plan.status = "ready" if not plan.blockers else "blocked"
    return plan


def analyze_restriction_digest(
    molecule_id: str,
    sequence: str,
    enzyme_names: list[str],
    *,
    circular: bool,
    issues: list[PlanIssue] | None = None,
) -> list[RestrictionDigest]:
    analyses: list[RestrictionDigest] = []
    for enzyme_name in enzyme_names:
        enzyme = _restriction_enzyme(enzyme_name)
        if enzyme is None:
            if issues is not None:
                issues.append(
                    PlanIssue(
                        code="UNKNOWN_RESTRICTION_ENZYME",
                        message=f"Unknown restriction enzyme: {enzyme_name}.",
                        severity="error",
                        subject_id=enzyme_name,
                    )
                )
            continue
        cuts = [int(value) for value in enzyme.search(Seq(sequence), linear=not circular)]
        analyses.append(
            RestrictionDigest(
                molecule_id=molecule_id,
                enzyme=enzyme_name,
                recognition_site=str(enzyme.site),
                cut_positions=cuts,
                fragment_lengths=_digest_fragment_lengths(
                    len(sequence),
                    cuts,
                    circular=circular,
                ),
                circular=circular,
            )
        )
    return analyses


def _plan_gibson(
    plan: AssemblyPlan,
    backbone_core: str,
    insert_sequence: str,
    *,
    overlap_length: int,
) -> None:
    if overlap_length < 15 or overlap_length > 80:
        plan.issues.append(
            PlanIssue(
                code="INVALID_GIBSON_OVERLAP_LENGTH",
                message="Gibson overlap length must be between 15 and 80 bp.",
                severity="error",
            )
        )
        return
    if len(backbone_core) < overlap_length * 2:
        plan.issues.append(
            PlanIssue(
                code="BACKBONE_TOO_SHORT_FOR_GIBSON",
                message="Backbone core is too short for the requested overlaps.",
                severity="error",
            )
        )
        return
    left_overlap = backbone_core[-overlap_length:]
    right_overlap = backbone_core[:overlap_length]
    insert_fragment = AssemblyFragment(
        fragment_id="insert_fragment",
        name="Insert with Gibson overlaps",
        source_type="insert",
        sequence=left_overlap + insert_sequence + right_overlap,
        core_sequence=insert_sequence,
        left_adapter=left_overlap,
        right_adapter=right_overlap,
    )
    backbone_fragment = AssemblyFragment(
        fragment_id="backbone_fragment",
        name="Linearized backbone",
        source_type="backbone",
        sequence=right_overlap + backbone_core + left_overlap,
        core_sequence=backbone_core,
        left_adapter=right_overlap,
        right_adapter=left_overlap,
    )
    plan.fragments.extend([backbone_fragment, insert_fragment])
    junction_specs = [
        ("junction_backbone_insert", "backbone_fragment", "insert_fragment", left_overlap),
        ("junction_insert_backbone", "insert_fragment", "backbone_fragment", right_overlap),
    ]
    combined_source = backbone_core + insert_sequence
    for junction_id, left_id, right_id, overlap in junction_specs:
        unique = _sequence_occurrences(combined_source, overlap) == 1
        tm_val = round(_calculate_tm(overlap), 2)
        plan.junctions.append(
            AssemblyJunction(
                junction_id=junction_id,
                left_fragment_id=left_id,
                right_fragment_id=right_id,
                junction_type="homology",
                sequence=overlap,
                unique=unique,
                metadata={"tm": tm_val},
            )
        )
        plan.scars.append(
            AssemblyScar(
                scar_id=f"scar_{junction_id}",
                junction_id=junction_id,
                sequence=overlap,
                scar_type="seamless_homology",
                retained_in_product=False,
                note="Overlap is not duplicated in the assembled product.",
            )
        )
        if tm_val < 55.0 or tm_val > 72.0:
            plan.issues.append(
                PlanIssue(
                    code="GIBSON_OVERLAP_TM_OUT_OF_RANGE",
                    message=(
                        f"Gibson overlap for {junction_id} has Tm of {tm_val:.1f} C "
                        f"(recommended: 55-72 C)."
                    ),
                    severity="warning",
                    subject_id=junction_id,
                )
            )
        if not unique:
            plan.issues.append(
                PlanIssue(
                    code="GIBSON_OVERLAP_NOT_UNIQUE",
                    message=f"Overlap {junction_id} is not unique in source sequences.",
                    severity="error",
                    subject_id=junction_id,
                )
            )
    try:
        products = Assembly(
            [
                Dseqrecord(backbone_fragment.sequence),
                Dseqrecord(insert_fragment.sequence),
            ],
            limit=overlap_length,
            use_fragment_order=True,
            use_all_fragments=True,
        ).assemble_circular(only_adjacent_edges=True, max_assemblies=100)
    except (ValueError, ImportError, Exception) as exc:
        products = []
        plan.issues.append(
            PlanIssue(
                code="PYDNA_GIBSON_VALIDATION_FAILED",
                message=f"pydna could not validate the Gibson plan: {exc}",
                severity="error",
            )
        )
    plan.method_details.update(
        {
            "overlap_length": overlap_length,
            "pydna_circular_product_count": len(products),
            "pydna_product_lengths": sorted({len(product) for product in products}),
        }
    )
    if not products:
        plan.issues.append(
            PlanIssue(
                code="PYDNA_GIBSON_NO_PRODUCT",
                message="pydna did not find a circular product for the planned fragments.",
                severity="error",
            )
        )


def _plan_golden_gate(
    plan: AssemblyPlan,
    backbone_core: str,
    insert_sequence: str,
    *,
    enzyme_name: str,
    requested_overhangs: list[str] | None,
) -> None:
    if enzyme_name not in TYPE_IIS_ENZYMES:
        plan.issues.append(
            PlanIssue(
                code="UNSUPPORTED_TYPE_IIS_ENZYME",
                message="Golden Gate v1 supports BsaI or BsmBI.",
                severity="error",
                subject_id=enzyme_name,
            )
        )
        return
    enzyme = _restriction_enzyme(enzyme_name)
    assert enzyme is not None
    for molecule_id, sequence in (
        ("backbone", backbone_core),
        ("insert", insert_sequence),
    ):
        sites = enzyme.search(Seq(sequence), linear=True)
        if sites:
            plan.issues.append(
                PlanIssue(
                    code="TYPE_IIS_INTERNAL_SITE",
                    message=(
                        f"{molecule_id} contains internal {enzyme_name} site(s) "
                        f"at {', '.join(str(value) for value in sites)}."
                    ),
                    severity="error",
                    subject_id=molecule_id,
                )
            )
    overhangs = requested_overhangs or list(DEFAULT_GOLDEN_GATE_OVERHANGS[:2])
    normalized = [str(value).upper() for value in overhangs]
    if len(normalized) != 2:
        plan.issues.append(
            PlanIssue(
                code="GOLDEN_GATE_OVERHANG_COUNT",
                message="Two overhangs are required for backbone-insert assembly.",
                severity="error",
            )
        )
        return
    for overhang in normalized:
        if not re.fullmatch(r"[ACGT]{4}", overhang):
            plan.issues.append(
                PlanIssue(
                    code="INVALID_GOLDEN_GATE_OVERHANG",
                    message=f"Invalid four-base overhang: {overhang}.",
                    severity="error",
                    subject_id=overhang,
                )
            )
    if not _overhangs_unique_and_directional(normalized):
        plan.issues.append(
            PlanIssue(
                code="GOLDEN_GATE_OVERHANG_CONFLICT",
                message="Overhangs are duplicated or reverse-complement conflicts.",
                severity="error",
            )
        )
    if plan.blockers:
        plan.method_details.update(
            {
                "enzyme": enzyme_name,
                "recognition_site": str(enzyme.site),
                "overhangs": normalized,
                "directional": _overhangs_unique_and_directional(normalized),
            }
        )
        return
    left_overhang, right_overhang = normalized
    recognition = str(enzyme.site)
    recognition_rc = str(Seq(recognition).reverse_complement())
    spacer = "A"
    backbone_fragment = AssemblyFragment(
        fragment_id="backbone_fragment",
        name=f"Backbone prepared for {enzyme_name}",
        source_type="backbone",
        sequence=(
            recognition
            + spacer
            + right_overhang
            + backbone_core
            + left_overhang
            + spacer
            + recognition_rc
        ),
        core_sequence=backbone_core,
        left_adapter=recognition + spacer + right_overhang,
        right_adapter=left_overhang + spacer + recognition_rc,
    )
    insert_fragment = AssemblyFragment(
        fragment_id="insert_fragment",
        name=f"Insert prepared for {enzyme_name}",
        source_type="insert",
        sequence=(
            recognition
            + spacer
            + left_overhang
            + insert_sequence
            + right_overhang
            + spacer
            + recognition_rc
        ),
        core_sequence=insert_sequence,
        left_adapter=recognition + spacer + left_overhang,
        right_adapter=right_overhang + spacer + recognition_rc,
    )
    plan.fragments.extend([backbone_fragment, insert_fragment])
    for junction_id, left_id, right_id, overhang in (
        ("junction_backbone_insert", "backbone_fragment", "insert_fragment", left_overhang),
        ("junction_insert_backbone", "insert_fragment", "backbone_fragment", right_overhang),
    ):
        plan.junctions.append(
            AssemblyJunction(
                junction_id=junction_id,
                left_fragment_id=left_id,
                right_fragment_id=right_id,
                junction_type="sticky_end",
                sequence=overhang,
                unique=normalized.count(overhang) == 1,
                direction_valid=_overhangs_unique_and_directional(normalized),
                metadata={"enzyme": enzyme_name},
            )
        )
        plan.scars.append(
            AssemblyScar(
                scar_id=f"scar_{junction_id}",
                junction_id=junction_id,
                sequence=overhang,
                scar_type="golden_gate_fusion",
                retained_in_product=True,
                note="Fusion overhang remains at the part junction.",
            )
        )
    try:
        products = golden_gate_assembly(
            [
                Dseqrecord(backbone_fragment.sequence),
                Dseqrecord(insert_fragment.sequence),
            ],
            [enzyme],
            allow_blunt=False,
            circular_only=True,
        )
    except ValueError as exc:
        products = []
        plan.issues.append(
            PlanIssue(
                code="PYDNA_GOLDEN_GATE_VALIDATION_FAILED",
                message=f"pydna could not validate the Golden Gate plan: {exc}",
                severity="error",
            )
        )
    plan.method_details.update(
        {
            "enzyme": enzyme_name,
            "recognition_site": recognition,
            "overhangs": normalized,
            "directional": _overhangs_unique_and_directional(normalized),
            "pydna_circular_product_count": len(products),
            "pydna_product_lengths": sorted({len(product) for product in products}),
            "planned_scarred_product_length": (
                len(backbone_core) + len(insert_sequence) + sum(map(len, normalized))
            ),
        }
    )
    if not products:
        plan.issues.append(
            PlanIssue(
                code="PYDNA_GOLDEN_GATE_NO_PRODUCT",
                message="pydna did not find a circular Golden Gate product.",
                severity="error",
            )
        )


def _plan_restriction_cloning(
    plan: AssemblyPlan,
    backbone_core: str,
    insert_sequence: str,
    *,
    enzymes: list[str],
) -> None:
    selected = []
    for enzyme_name in enzymes:
        enzyme = _restriction_enzyme(enzyme_name)
        if enzyme is None or enzyme_name in TYPE_IIS_ENZYMES:
            continue
        backbone_sites = enzyme.search(Seq(backbone_core), linear=True)
        insert_sites = enzyme.search(Seq(insert_sequence), linear=True)
        if len(backbone_sites) == 1 and not insert_sites:
            selected.append(enzyme_name)
    if len(selected) < 2:
        plan.issues.append(
            PlanIssue(
                code="RESTRICTION_PAIR_NOT_FOUND",
                message=(
                    "No two supplied enzymes cut the backbone core exactly once "
                    "while leaving the insert uncut."
                ),
                severity="error",
            )
        )
        return
    pair = selected[:2]
    plan.fragments.extend(
        [
            AssemblyFragment(
                fragment_id="backbone_fragment",
                name="Restriction-digested backbone",
                source_type="backbone",
                sequence=backbone_core,
                core_sequence=backbone_core,
                metadata={"enzymes": pair},
            ),
            AssemblyFragment(
                fragment_id="insert_fragment",
                name="Restriction-compatible insert",
                source_type="insert",
                sequence=insert_sequence,
                core_sequence=insert_sequence,
                metadata={"enzymes": pair},
            ),
        ]
    )
    for index, enzyme_name in enumerate(pair, start=1):
        enzyme = _restriction_enzyme(enzyme_name)
        assert enzyme is not None
        junction_id = f"restriction_junction_{index}"
        plan.junctions.append(
            AssemblyJunction(
                junction_id=junction_id,
                left_fragment_id="backbone_fragment" if index == 1 else "insert_fragment",
                right_fragment_id="insert_fragment" if index == 1 else "backbone_fragment",
                junction_type="restriction_ligation",
                sequence=str(enzyme.site),
                unique=True,
                metadata={"enzyme": enzyme_name},
            )
        )
        plan.scars.append(
            AssemblyScar(
                scar_id=f"scar_{junction_id}",
                junction_id=junction_id,
                sequence=str(enzyme.site),
                scar_type="restriction_site",
                retained_in_product=True,
            )
        )
    plan.method_details["selected_enzymes"] = pair


def _restriction_enzyme(name: str):
    enzyme = getattr(Restriction, str(name), None)
    return enzyme if hasattr(enzyme, "search") else None


def _calculate_tm(sequence: str) -> float:
    if not sequence:
        return 0.0
    try:
        return float(mt.Tm_NN(Seq(sequence)))
    except Exception:
        seq = sequence.upper()
        gc = seq.count("G") + seq.count("C")
        at = seq.count("A") + seq.count("T")
        if (gc + at) == 0:
            return 0.0
        return round(64.9 + 41.0 * (gc - 16.4) / (gc + at), 2)


def _digest_fragment_lengths(
    sequence_length: int,
    cut_positions: list[int],
    *,
    circular: bool,
) -> list[int]:
    if not cut_positions:
        return [sequence_length]
    cuts = sorted({max(0, min(sequence_length, value - 1)) for value in cut_positions})
    if circular:
        if len(cuts) == 1:
            return [sequence_length]
        lengths = [
            right - left
            for left, right in pairwise(cuts)
        ]
        lengths.append(sequence_length - cuts[-1] + cuts[0])
        return sorted(lengths, reverse=True)
    boundaries = [0, *cuts, sequence_length]
    return sorted(
        [right - left for left, right in pairwise(boundaries)],
        reverse=True,
    )


def _sequence_occurrences(sequence: str, motif: str) -> int:
    forward = len(re.findall(f"(?={re.escape(motif)})", sequence))
    reverse = str(Seq(motif).reverse_complement())
    if reverse == motif:
        return forward
    return forward + len(re.findall(f"(?={re.escape(reverse)})", sequence))


def _overhangs_unique_and_directional(overhangs: list[str]) -> bool:
    if len(set(overhangs)) != len(overhangs):
        return False
    reverse_complements = {
        str(Seq(overhang).reverse_complement())
        for overhang in overhangs
    }
    return not any(
        reverse in set(overhangs)
        for reverse in reverse_complements
    )
