from __future__ import annotations

from dataclasses import asdict, dataclass, field
from io import StringIO
import re
from typing import Any

from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqFeature import CompoundLocation, FeatureLocation, SeqFeature
from Bio.SeqRecord import SeqRecord
from pydna.dseqrecord import Dseqrecord

from exporters.sequence_utils import is_valid_iupac_dna, normalize_dna
from schemas.backbone_registry import BackboneRegistryEntry, sequence_checksum
from schemas.design_ir_v2 import (
    BiologicalPartV2,
    ConstructV2,
    DesignIRV2,
    PlasmidV2,
)
from utils.package_metadata import package_version as _package_version


FEATURE_TYPES = {
    "promoter": "promoter",
    "rbs": "RBS",
    "cds": "CDS",
    "terminator": "terminator",
    "rep_origin": "rep_origin",
}
RESTRICTION_SITES = {
    "BsaI": ("GGTCTC", "GAGACC"),
    "BsmBI": ("CGTCTC", "GAGACG"),
    "EcoRI": ("GAATTC",),
    "XbaI": ("TCTAGA",),
    "SpeI": ("ACTAGT",),
    "PstI": ("CTGCAG",),
}
ORIENTATIONS = {
    "forward": 1,
    "+": 1,
    "reverse": -1,
    "-": -1,
    "reverse_complement": -1,
}
ASSEMBLY_EVIDENCE_LEVELS = {
    "database_derived",
    "literature_supported",
    "experimentally_characterized",
    "user_verified",
}


@dataclass
class AssemblyIssue:
    code: str
    message: str
    severity: str = "warning"
    subject_id: str | None = None


@dataclass
class AssemblyReport:
    status: str
    design_id: str
    plasmid_id: str
    backbone_name: str
    assembly_method: str
    insertion_start: int
    insertion_end: int
    backbone_length: int
    insert_length: int
    assembled_length: int
    construct_ids: list[str]
    removed_backbone_features: list[str] = field(default_factory=list)
    restriction_sites: dict[str, list[int]] = field(default_factory=dict)
    issues: list[AssemblyIssue] = field(default_factory=list)
    external_tools: dict[str, str] = field(default_factory=dict)
    pydna_circular: bool = False
    sequence_checksum: str | None = None
    backbone_id: str | None = None
    backbone_version: str | None = None
    backbone_checksum: str | None = None
    insertion_region_id: str | None = None
    readiness_status: str = "conceptual"
    readiness_history: list[str] = field(default_factory=lambda: ["conceptual"])

    @property
    def blockers(self) -> list[AssemblyIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["blockers"] = [asdict(issue) for issue in self.blockers]
        return payload


@dataclass
class PlasmidAssemblyResult:
    ok: bool
    filename: str
    genbank: str
    report: AssemblyReport

    def to_dict(self, *, include_genbank: bool = True) -> dict[str, Any]:
        payload = {
            "ok": self.ok,
            "filename": self.filename,
            "report": self.report.to_dict(),
        }
        if include_genbank:
            payload["genbank"] = self.genbank
        return payload


def assemble_plasmid_v2(
    design: DesignIRV2,
    *,
    plasmid_id: str,
    backbone_genbank: str | bytes,
    insertion_start: int,
    insertion_end: int,
    assembly_method: str = "direct_insertion",
    backbone_entry: BackboneRegistryEntry | None = None,
    insertion_region_id: str | None = None,
) -> PlasmidAssemblyResult:
    plasmid = _find_plasmid(design, plasmid_id)
    record = _read_backbone(backbone_genbank)
    backbone_length = len(record.seq)
    _validate_insertion_window(insertion_start, insertion_end, backbone_length)

    report = AssemblyReport(
        status="validating",
        design_id=design.design_id,
        plasmid_id=plasmid.id,
        backbone_name=record.name or record.id,
        assembly_method=assembly_method,
        insertion_start=insertion_start,
        insertion_end=insertion_end,
        backbone_length=backbone_length,
        insert_length=0,
        assembled_length=0,
        construct_ids=list(plasmid.construct_ids),
        external_tools={
            "biopython": _package_version("biopython"),
            "pydna": _package_version("pydna"),
        },
        backbone_id=backbone_entry.backbone_id if backbone_entry else None,
        backbone_version=backbone_entry.version if backbone_entry else None,
        backbone_checksum=(
            backbone_entry.sequence_checksum if backbone_entry else None
        ),
        insertion_region_id=insertion_region_id,
    )
    if backbone_entry:
        _validate_registered_backbone(
            design,
            record,
            backbone_entry,
            insertion_start=insertion_start,
            insertion_end=insertion_end,
            insertion_region_id=insertion_region_id,
            report=report,
        )
    insert_sequence, insert_features = _build_insert(
        design,
        plasmid,
        insertion_start=insertion_start,
        report=report,
    )
    report.insert_length = len(insert_sequence)
    if not report.blockers:
        _advance_readiness(report, "sequence_complete")
        _advance_readiness(report, "assembly_method_selected")
    if report.blockers:
        report.status = "blocked"
        return PlasmidAssemblyResult(
            ok=False,
            filename=f"{_filename_token(plasmid.id)}_blocked.gb",
            genbank="",
            report=report,
        )

    replacement_length = insertion_end - insertion_start
    delta = len(insert_sequence) - replacement_length
    backbone_features = _remap_backbone_features(
        record.features,
        insertion_start=insertion_start,
        insertion_end=insertion_end,
        delta=delta,
        report=report,
    )
    assembled_sequence = (
        str(record.seq[:insertion_start])
        + insert_sequence
        + str(record.seq[insertion_end:])
    )
    assembled = SeqRecord(
        Seq(assembled_sequence),
        id=_record_token(plasmid.id),
        name=_record_token(plasmid.name)[:16],
        description=(
            f"{plasmid.name}; assembled from {record.id} using "
            f"{assembly_method}."
        ),
        annotations=dict(record.annotations),
        features=backbone_features + insert_features,
        dbxrefs=list(record.dbxrefs),
    )
    assembled.annotations["molecule_type"] = "DNA"
    assembled.annotations["topology"] = "circular"
    assembled.annotations["data_file_division"] = "SYN"
    assembled.annotations["design_id"] = design.design_id
    assembled.annotations["plasmid_id"] = plasmid.id

    report.assembled_length = len(assembled.seq)
    report.restriction_sites = _restriction_site_positions(assembled_sequence)
    _append_sequence_warnings(design, plasmid, assembly_method, report)
    molecule = Dseqrecord(assembled, circular=True)
    report.pydna_circular = bool(molecule.circular)
    report.sequence_checksum = molecule.seguid()
    report.status = (
        "assembly_check_passed" if not report.blockers else "blocked"
    )
    if not report.blockers:
        _advance_readiness(report, "assembly_check_passed")
    output = StringIO()
    SeqIO.write(assembled, output, "genbank")
    return PlasmidAssemblyResult(
        ok=not report.blockers,
        filename=f"{_filename_token(plasmid.id)}_assembled.gb",
        genbank=output.getvalue(),
        report=report,
    )


def _find_plasmid(design: DesignIRV2, plasmid_id: str) -> PlasmidV2:
    plasmid = next(
        (item for item in design.plasmids if item.id == plasmid_id),
        None,
    )
    if plasmid is None:
        raise ValueError(f"Unknown plasmid ID: {plasmid_id}")
    if not plasmid.construct_ids:
        raise ValueError(f"Plasmid {plasmid_id} has no constructs.")
    return plasmid


def _read_backbone(value: str | bytes) -> SeqRecord:
    text = value.decode("utf-8-sig") if isinstance(value, bytes) else str(value)
    if not text.strip():
        raise ValueError("Backbone GenBank content is empty.")
    try:
        records = list(SeqIO.parse(StringIO(text), "genbank"))
    except Exception as exc:
        raise ValueError(f"Invalid backbone GenBank: {exc}") from exc
    if len(records) != 1:
        raise ValueError("Backbone GenBank must contain exactly one record.")
    record = records[0]
    sequence = normalize_dna(str(record.seq))
    if not sequence or not is_valid_iupac_dna(sequence):
        raise ValueError("Backbone contains invalid or missing IUPAC DNA.")
    record.seq = Seq(sequence)
    return record


def _validate_insertion_window(start: int, end: int, length: int) -> None:
    if start < 0 or end < 0:
        raise ValueError("Insertion coordinates cannot be negative.")
    if start > end:
        raise ValueError("insertion_start must be less than or equal to insertion_end.")
    if end > length:
        raise ValueError(
            f"Insertion window {start}:{end} exceeds backbone length {length}."
        )


def _build_insert(
    design: DesignIRV2,
    plasmid: PlasmidV2,
    *,
    insertion_start: int,
    report: AssemblyReport,
) -> tuple[str, list[SeqFeature]]:
    part_map = {part.id: part for part in design.parts}
    construct_map = {construct.id: construct for construct in design.constructs}
    sequence_parts: list[str] = []
    features: list[SeqFeature] = []
    offset = insertion_start
    for construct_id in plasmid.construct_ids:
        construct = construct_map.get(construct_id)
        if construct is None:
            report.issues.append(
                AssemblyIssue(
                    code="UNKNOWN_CONSTRUCT",
                    message=f"Plasmid references unknown construct {construct_id}.",
                    severity="error",
                    subject_id=construct_id,
                )
            )
            continue
        offset = _append_construct(
            construct,
            part_map,
            offset=offset,
            sequence_parts=sequence_parts,
            features=features,
            report=report,
        )
    return "".join(sequence_parts), features


def _append_construct(
    construct: ConstructV2,
    part_map: dict[str, BiologicalPartV2],
    *,
    offset: int,
    sequence_parts: list[str],
    features: list[SeqFeature],
    report: AssemblyReport,
) -> int:
    for instance in sorted(construct.part_instances, key=lambda item: item.order):
        part = part_map.get(instance.part_id)
        if part is None:
            report.issues.append(
                AssemblyIssue(
                    code="UNKNOWN_PART",
                    message=f"Construct {construct.id} references unknown part {instance.part_id}.",
                    severity="error",
                    subject_id=instance.part_id,
                )
            )
            continue
        sequence = normalize_dna(part.sequence)
        if not sequence:
            report.issues.append(
                AssemblyIssue(
                    code="MISSING_SEQUENCE",
                    message=f"Part {part.id} has no sequence.",
                    severity="error",
                    subject_id=part.id,
                )
            )
            continue
        if not is_valid_iupac_dna(sequence):
            report.issues.append(
                AssemblyIssue(
                    code="INVALID_SEQUENCE",
                    message=f"Part {part.id} contains non-IUPAC DNA characters.",
                    severity="error",
                    subject_id=part.id,
                )
            )
            continue
        evidence_level = str(part.evidence_level or "unknown").lower()
        if evidence_level not in ASSEMBLY_EVIDENCE_LEVELS:
            report.issues.append(
                AssemblyIssue(
                    code="PART_EVIDENCE_INSUFFICIENT",
                    message=(
                        f"Part {part.id} evidence level {evidence_level!r} "
                        "is not accepted for assembly."
                    ),
                    severity="error",
                    subject_id=part.id,
                )
            )
            continue
        orientation = str(instance.orientation or "forward").lower()
        strand = ORIENTATIONS.get(orientation)
        if strand is None:
            report.issues.append(
                AssemblyIssue(
                    code="INVALID_ORIENTATION",
                    message=f"Part instance {instance.instance_id} has invalid orientation {instance.orientation}.",
                    severity="error",
                    subject_id=instance.instance_id,
                )
            )
            continue
        selected_sequence = (
            sequence if strand == 1 else str(Seq(sequence).reverse_complement())
        )
        end = offset + len(selected_sequence)
        sequence_parts.append(selected_sequence)
        features.append(
            SeqFeature(
                FeatureLocation(offset, end, strand=strand),
                type=FEATURE_TYPES.get(part.part_type.lower(), "misc_feature"),
                qualifiers={
                    "label": [part.name],
                    "part_id": [part.id],
                    "instance_id": [instance.instance_id],
                    "construct_id": [construct.id],
                    "role": [part.role],
                    "source_library": [part.source],
                    "orientation": ["forward" if strand == 1 else "reverse"],
                    "provenance": list(part.provenance_ids),
                    "evidence_level": [evidence_level],
                },
            )
        )
        offset = end
    return offset


def _remap_backbone_features(
    features: list[SeqFeature],
    *,
    insertion_start: int,
    insertion_end: int,
    delta: int,
    report: AssemblyReport,
) -> list[SeqFeature]:
    remapped: list[SeqFeature] = []
    for feature in features:
        parts = list(getattr(feature.location, "parts", [feature.location]))
        if any(
            int(part.start) < insertion_end and int(part.end) > insertion_start
            for part in parts
        ):
            report.removed_backbone_features.append(_feature_label(feature))
            continue
        shifted_parts = [
            FeatureLocation(
                int(part.start) + (delta if int(part.start) >= insertion_end else 0),
                int(part.end) + (delta if int(part.start) >= insertion_end else 0),
                strand=part.strand,
            )
            for part in parts
        ]
        location = (
            shifted_parts[0]
            if len(shifted_parts) == 1
            else CompoundLocation(
                shifted_parts,
                operator=getattr(feature.location, "operator", "join"),
            )
        )
        remapped.append(
            SeqFeature(
                location=location,
                type=feature.type,
                id=feature.id,
                qualifiers=dict(feature.qualifiers),
            )
        )
    return remapped


def _append_sequence_warnings(
    design: DesignIRV2,
    plasmid: PlasmidV2,
    assembly_method: str,
    report: AssemblyReport,
) -> None:
    from benchmark_suite.layout_critic import analyze_layout_issues
    layout_issues = analyze_layout_issues(design, plasmid)
    report.issues.extend(layout_issues)

    host = str(design.biological_context.host_organism.value or "").lower()
    part_ids = {
        instance.part_id
        for construct in design.constructs
        if construct.id in plasmid.construct_ids
        for instance in construct.part_instances
    }
    for part in design.parts:
        if part.id not in part_ids:
            continue
        sequence = normalize_dna(part.sequence) or ""
        if part.part_type.lower() == "cds":
            if len(sequence) % 3:
                report.issues.append(
                    AssemblyIssue(
                        code="CDS_FRAME_LENGTH",
                        message=f"CDS {part.id} length is not divisible by three.",
                        subject_id=part.id,
                    )
                )
            if sequence and not sequence.startswith("ATG"):
                report.issues.append(
                    AssemblyIssue(
                        code="CDS_START_CODON",
                        message=f"CDS {part.id} does not start with ATG.",
                        subject_id=part.id,
                    )
                )
            if sequence and sequence[-3:] not in {"TAA", "TAG", "TGA"}:
                report.issues.append(
                    AssemblyIssue(
                        code="CDS_STOP_CODON",
                        message=f"CDS {part.id} has no terminal stop codon.",
                        subject_id=part.id,
                    )
                )
        compatible = {item.lower() for item in part.host_compatibility}
        if host and compatible and host not in compatible:
            report.issues.append(
                AssemblyIssue(
                    code="HOST_COMPATIBILITY",
                    message=f"Part {part.id} is not annotated for host {host}.",
                    subject_id=part.id,
                )
            )
    if assembly_method.lower() == "gibson":
        report.issues.append(
            AssemblyIssue(
                code="GIBSON_OVERLAPS_NOT_DESIGNED",
                message=(
                    "The assembled molecule is sequence-complete, but Gibson "
                    "homology arms and primers have not been designed."
                ),
            )
        )


def _validate_registered_backbone(
    design: DesignIRV2,
    record: SeqRecord,
    entry: BackboneRegistryEntry,
    *,
    insertion_start: int,
    insertion_end: int,
    insertion_region_id: str | None,
    report: AssemblyReport,
) -> None:
    actual_checksum = sequence_checksum(str(record.seq))
    if actual_checksum != entry.sequence_checksum:
        report.issues.append(
            AssemblyIssue(
                code="BACKBONE_CHECKSUM_MISMATCH",
                message="Registered backbone checksum no longer matches its sequence.",
                severity="error",
                subject_id=entry.registry_key,
            )
        )
    selected_region = next(
        (
            region
            for region in entry.insertion_regions
            if region.region_id == insertion_region_id
        ),
        None,
    )
    if selected_region is None:
        report.issues.append(
            AssemblyIssue(
                code="UNKNOWN_INSERTION_REGION",
                message=(
                    f"Insertion region {insertion_region_id!r} is not registered "
                    f"for backbone {entry.registry_key}."
                ),
                severity="error",
                subject_id=insertion_region_id,
            )
        )
    elif not selected_region.contains(insertion_start, insertion_end):
        report.issues.append(
            AssemblyIssue(
                code="INSERTION_OUTSIDE_LEGAL_REGION",
                message=(
                    f"Insertion window {insertion_start}:{insertion_end} is "
                    f"outside legal region {selected_region.region_id} "
                    f"({selected_region.start}:{selected_region.end})."
                ),
                severity="error",
                subject_id=selected_region.region_id,
            )
        )
    for essential in entry.essential_regions:
        if essential.overlaps(insertion_start, insertion_end):
            report.issues.append(
                AssemblyIssue(
                    code="ESSENTIAL_FEATURE_PROTECTED",
                    message=(
                        f"Insertion window overlaps essential region "
                        f"{essential.name} ({essential.start}:{essential.end})."
                    ),
                    severity="error",
                    subject_id=essential.region_id,
                )
            )
    host = str(design.biological_context.host_organism.value or "").lower()
    compatible_hosts = {item.lower() for item in entry.host_organisms}
    if host and host not in compatible_hosts:
        report.issues.append(
            AssemblyIssue(
                code="BACKBONE_HOST_INCOMPATIBLE",
                message=(
                    f"Backbone {entry.registry_key} is not registered for "
                    f"host {host}."
                ),
                severity="error",
                subject_id=entry.registry_key,
            )
        )


def _advance_readiness(report: AssemblyReport, status: str) -> None:
    report.readiness_status = status
    if not report.readiness_history or report.readiness_history[-1] != status:
        report.readiness_history.append(status)


def _restriction_site_positions(sequence: str) -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    upper = sequence.upper()
    for enzyme, motifs in RESTRICTION_SITES.items():
        positions = sorted(
            {
                match.start() + 1
                for motif in motifs
                for match in re.finditer(f"(?={motif})", upper)
            }
        )
        if positions:
            found[enzyme] = positions
    return found


def _feature_label(feature: SeqFeature) -> str:
    for key in ("label", "gene", "locus_tag"):
        value = feature.qualifiers.get(key)
        if value:
            return str(value[0])
    return feature.type


def _record_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value).strip("_") or "plasmid"


def _filename_token(value: str) -> str:
    return _record_token(value).replace(".", "_")
