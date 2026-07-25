from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
from typing import Any


SAFETY_BOUNDARY_VERSION = "phase8-lite-v1"
AUDIT_LOG_DIR = Path("outputs/api_data")
AUDIT_LOG_FILE = AUDIT_LOG_DIR / "safety_audit.json"
_AUDIT_LOCK = threading.RLock()

BLOCKED_CHASSIS_PATTERNS = [
    (r"yersinia\s+pestis|鼠疫耶爾森菌|鼠疫桿菌", "Yersinia pestis"),
    (r"bacillus\s+anthracis|炭疽芽孢桿菌|炭疽桿菌", "Bacillus anthracis"),
    (r"vibrio\s+cholerae|霍亂弧菌", "Vibrio cholerae"),
    (r"francisella\s+tularensis|土拉弗朗西斯菌|兔熱病菌", "Francisella tularensis"),
    (r"variola\s+virus|天花病毒", "Variola virus"),
    (r"ebola\s+virus|伊波拉病毒|埃博拉病毒", "Ebola virus"),
    (r"marburg\s+virus|馬堡病毒|马尔堡病毒", "Marburg virus"),
    (r"lassa\s+virus|拉薩病毒|拉沙病毒", "Lassa virus"),
    (r"burkholderia\s+mallei|鼻疽伯克霍爾德菌", "Burkholderia mallei"),
    (r"burkholderia\s+pseudomallei|類鼻疽伯克霍爾德菌", "Burkholderia pseudomallei"),
    (r"rickettsia\s+prowazekii|普氏立克次體", "Rickettsia prowazekii"),
    (r"coccidioides\s+(?:immitis|posadasii)|球孢子菌", "Coccidioides species"),
    (r"foot-and-mouth\s+disease\s+virus|口蹄疫病毒", "Foot-and-mouth disease virus"),
    (r"african\s+swine\s+fever\s+virus|非洲豬瘟病毒", "African swine fever virus"),
    (r"rinderpest\s+virus|牛瘟病毒", "Rinderpest virus"),
]

BLOCKED_TOXIN_PATTERNS = [
    (r"\bricin\b|蓖麻毒素", "ricin"),
    (r"botulinum|肉毒(?:桿菌)?毒素", "botulinum toxin"),
    (r"diphtheria\s+toxin|白喉毒素", "diphtheria toxin"),
    (r"tetanus\s+toxin|破傷風毒素", "tetanus toxin"),
    (r"saxitoxin|石房蛤毒素|麻痺性貝毒", "saxitoxin"),
    (r"\babrin\b|相思豆毒素", "abrin"),
    (r"staphylococcal\s+enterotoxin|葡萄球菌腸毒素", "staphylococcal enterotoxin"),
    (r"t-2\s+toxin|t-2\s*毒素", "T-2 toxin"),
    (r"conotoxin|芋螺毒素", "conotoxin"),
    (r"gonyautoxin|膝溝藻毒素", "gonyautoxin"),
    (r"shiga\s+toxin|志賀毒素", "Shiga toxin"),
]

WARN_CHASSIS_PATTERNS = [
    (r"pseudomonas\s+aeruginosa|綠膿桿菌|銅綠假單胞菌", "Pseudomonas aeruginosa"),
    (r"staphylococcus\s+aureus|金黃色葡萄球菌", "Staphylococcus aureus"),
    (r"streptococcus\s+pneumoniae|肺炎鏈球菌", "Streptococcus pneumoniae"),
    (r"salmonella\s+enterica|腸道沙門氏菌", "Salmonella enterica"),
    (r"klebsiella\s+pneumoniae|肺炎克雷伯菌", "Klebsiella pneumoniae"),
    (r"acinetobacter\s+baumannii|鮑氏不動桿菌", "Acinetobacter baumannii"),
    (r"shigella\s+flexneri|福氏志賀菌", "Shigella flexneri"),
    (r"enterococcus\s+faecalis|糞腸球菌", "Enterococcus faecalis"),
    (r"neisseria\s+meningitidis|腦膜炎雙球菌", "Neisseria meningitidis"),
    (r"haemophilus\s+influenzae|流感嗜血桿菌", "Haemophilus influenzae"),
    (r"clostridioides\s+difficile|clostridium\s+difficile|艱難梭菌", "Clostridioides difficile"),
    (r"mycobacterium\s+tuberculosis|結核分枝桿菌", "Mycobacterium tuberculosis"),
]

WARN_KEYWORDS = [
    (r"\bvirulence\b|毒力(?:因子)?", "Design involves virulence-associated biology."),
    (r"\bpathogen(?:ic)?\b|病原(?:體|菌|性)?", "Design refers to pathogenic biology."),
    (r"\btoxin\b|毒素", "Design refers to expression or regulation of a biological toxin."),
    (
        r"antibiotic\s+resistance|resistance\s+gene|抗生素(?:抗性|耐藥性)|耐藥基因",
        "Design refers to antibiotic-resistance genes or context.",
    ),
]

SEQUENCE_CONTEXT_PATTERN = re.compile(
    r"dna\s+sequence|rna\s+sequence|genetic\s+sequence|nucleotide\s+sequence|"
    r"sequence-level|核酸序列|基因序列|dna\s*序列|rna\s*序列|序列層級|這段序列|该序列",
    re.IGNORECASE,
)
AUTOMATION_PATTERN = re.compile(
    r"automat(?:e|ed|ic|ically)|repair|edit|modify|optim(?:ize|ise|ization)|"
    r"synthesi[sz]e|assemble|export|download|自動|修復|編輯|修改|優化|最佳化|"
    r"合成|組裝|匯出|導出|下載",
    re.IGNORECASE,
)
SEQUENCE_LITERAL_PATTERN = re.compile(r"(?<![A-Za-z])[ACGTUNacgtun]{30,}(?![A-Za-z])")


@dataclass
class SafetyCheckResult:
    is_safe: bool
    status: str  # safe, warn, blocked
    warnings: list[str] = field(default_factory=list)
    redirection_message: str | None = None
    categories: list[str] = field(default_factory=list)
    requires_human_review: bool = False
    automation_allowed: bool = True
    export_allowed: bool = True
    claim_strength: str = "research_only"
    boundary_version: str = SAFETY_BOUNDARY_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_safety(user_intent: str, host_organism: str | None = None) -> SafetyCheckResult:
    intent = str(user_intent or "")
    host = str(host_organism or "")
    combined = f"{intent}\n{host}"

    for pattern, label in BLOCKED_CHASSIS_PATTERNS:
        if re.search(pattern, combined, flags=re.IGNORECASE):
            return _blocked_result(
                category="high_risk_chassis",
                warning=f"High-risk chassis match detected: {label}.",
                message=(
                    f"Design assistance involving the high-risk chassis '{label}' is blocked. "
                    "Use a benign teaching host or keep the discussion at a non-operational "
                    "biosafety-review level."
                ),
            )

    for pattern, label in BLOCKED_TOXIN_PATTERNS:
        if re.search(pattern, intent, flags=re.IGNORECASE):
            return _blocked_result(
                category="high_risk_toxin",
                warning=f"High-risk biological toxin match detected: {label}.",
                message=(
                    f"Design assistance to express or regulate the high-risk toxin '{label}' is "
                    "blocked. Use a benign reporter or request high-level safety information."
                ),
            )

    warnings: list[str] = []
    categories: list[str] = []
    for pattern, label in WARN_CHASSIS_PATTERNS:
        if re.search(pattern, combined, flags=re.IGNORECASE):
            categories.append("review_required_chassis")
            warnings.append(
                f"Review-required chassis detected: {label}. Confirm institutional biosafety "
                "approval before any laboratory planning."
            )

    for pattern, description in WARN_KEYWORDS:
        if re.search(pattern, intent, flags=re.IGNORECASE):
            categories.append("biosafety_context")
            warnings.append(description)

    has_sequence_context = bool(
        SEQUENCE_CONTEXT_PATTERN.search(intent) or SEQUENCE_LITERAL_PATTERN.search(intent)
    )
    has_automation_request = bool(AUTOMATION_PATTERN.search(intent))
    sequence_automation = has_sequence_context and has_automation_request
    if sequence_automation:
        categories.append("sequence_automation")
        warnings.append(
            "Sequence-level repair, optimization, assembly, or export requires explicit human "
            "review; this request does not authorize an automated sequence action."
        )

    warnings = _deduplicate(warnings)
    categories = _deduplicate(categories)
    if warnings:
        warnings.append(
            "Research-preview output only: this design has not been validated in vivo and must "
            "not be treated as an experimental protocol."
        )
        return SafetyCheckResult(
            is_safe=True,
            status="warn",
            warnings=warnings,
            redirection_message=(
                "Sequence-level automation is paused pending explicit human safety review."
                if sequence_automation
                else None
            ),
            categories=categories,
            requires_human_review=True,
            automation_allowed=not sequence_automation,
            export_allowed=not sequence_automation,
        )

    return SafetyCheckResult(is_safe=True, status="safe")


def check_design_export_safety(
    design_payload: dict[str, Any],
    export_format: str,
) -> SafetyCheckResult:
    specification = _mapping(design_payload.get("specification"))
    biological_context = _mapping(design_payload.get("biological_context"))
    host = _attributed_text(biological_context.get("host_organism")) or _attributed_text(
        biological_context.get("chassis")
    )
    intent_parts = [
        str(specification.get("user_intent") or ""),
        str(design_payload.get("name") or ""),
    ]
    for part in _list_of_mappings(design_payload.get("parts")):
        intent_parts.extend(
            [str(part.get("name") or ""), str(part.get("role") or "")]
        )
    result = check_safety(" ".join(item for item in intent_parts if item), host)
    if result.status != "safe":
        result.warnings = _deduplicate(
            [f"Pre-export safety review for format '{export_format}'.", *result.warnings]
        )
    return result


def log_safety_event(
    run_id: str | None,
    intent: str,
    status: str,
    warnings: list[str],
    *,
    action: str = "intake",
    categories: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    raw_intent = str(intent or "")
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "boundary_version": SAFETY_BOUNDARY_VERSION,
        "run_id": run_id or "direct_check",
        "action": action,
        "intent_preview": _redact_sequences(raw_intent)[:1000],
        "intent_sha256": hashlib.sha256(raw_intent.encode("utf-8")).hexdigest(),
        "status": status,
        "categories": list(categories or []),
        "warnings": list(warnings),
        "metadata": dict(metadata or {}),
    }

    try:
        with _AUDIT_LOCK:
            AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)
            events = _read_audit_events()
            events.append(event)
            temporary = AUDIT_LOG_FILE.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(events, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary.replace(AUDIT_LOG_FILE)
    except OSError as exc:
        import sys

        print(f"Failed to write safety audit log: {exc}", file=sys.stderr)


def _blocked_result(*, category: str, warning: str, message: str) -> SafetyCheckResult:
    return SafetyCheckResult(
        is_safe=False,
        status="blocked",
        warnings=[warning],
        redirection_message=message,
        categories=[category],
        requires_human_review=True,
        automation_allowed=False,
        export_allowed=False,
    )


def _read_audit_events() -> list[dict[str, Any]]:
    if not AUDIT_LOG_FILE.exists():
        return []
    try:
        payload = json.loads(AUDIT_LOG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def _redact_sequences(value: str) -> str:
    return SEQUENCE_LITERAL_PATTERN.sub(
        lambda match: f"[sequence:redacted:length={len(match.group(0))}]",
        value,
    )


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_of_mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _attributed_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("value") or "")
    return str(value or "")
