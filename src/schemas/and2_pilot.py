from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from benchmark_suite.functional_scorer import score_functional


AND2_TRUTH_TABLE = [
    {"A": 0, "B": 0, "GFP": 0},
    {"A": 0, "B": 1, "GFP": 0},
    {"A": 1, "B": 0, "GFP": 0},
    {"A": 1, "B": 1, "GFP": 1},
]

FAILURE_RECORD_FIELDS = {
    "schema_version",
    "case_id",
    "attempt_id",
    "stage_id",
    "status",
    "failure_category",
    "command",
    "runtime_identity",
    "input_sha256s",
    "pre_input_sha256s",
    "post_input_sha256s",
    "input_hashes_equal",
    "exit_code",
    "elapsed_seconds",
    "provider_call_count",
    "paid_cost_usd",
    "cleanup_result",
    "artifact_inventory",
    "final_disposition",
}


class AttemptBudgetExceeded(RuntimeError):
    pass


@dataclass
class PilotAttemptBudget:
    max_provider_calls: int = 3
    max_cello_subprocesses: int = 1
    provider_calls: int = 0
    cello_subprocesses: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)

    def consume_provider(self, stage: str) -> None:
        if self.provider_calls >= self.max_provider_calls:
            raise AttemptBudgetExceeded(
                f"provider call cap exceeded before stage {stage}"
            )
        self.provider_calls += 1
        self.events.append(
            {"kind": "provider_call", "stage": stage, "ordinal": self.provider_calls}
        )

    def consume_cello(self, stage: str) -> None:
        if self.cello_subprocesses >= self.max_cello_subprocesses:
            raise AttemptBudgetExceeded(
                f"Cello subprocess cap exceeded before stage {stage}"
            )
        self.cello_subprocesses += 1
        self.events.append(
            {
                "kind": "cello_subprocess",
                "stage": stage,
                "ordinal": self.cello_subprocesses,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_provider_calls": self.max_provider_calls,
            "max_cello_subprocesses": self.max_cello_subprocesses,
            "provider_calls": self.provider_calls,
            "cello_subprocesses": self.cello_subprocesses,
            "events": list(self.events),
        }


def validate_and2_verilog(verilog: str) -> dict[str, Any]:
    code = re.sub(r"//.*?$|/\*.*?\*/", "", str(verilog or ""), flags=re.M | re.S)
    if re.search(r"\b(?:clk|clock|posedge|negedge|always|reg)\b", code, re.I):
        return _semantic_failure("Sequential or stateful Verilog is forbidden.")
    if "[" in code or "]" in code:
        return _semantic_failure(
            "Only scalar signals are allowed; vector dimensions are forbidden."
        )
    if (
        len(re.findall(r"\bmodule\b", code, re.I)) != 1
        or len(re.findall(r"\bendmodule\b", code, re.I)) != 1
    ):
        return _semantic_failure("Exactly one complete Verilog module is required.")
    inputs = _declaration_names(code, "input")
    outputs = _declaration_names(code, "output")
    if inputs != {"A", "B"} or outputs != {"GFP"}:
        return _semantic_failure(
            f"Interface must be exactly inputs A,B and output GFP; got inputs={sorted(inputs)}, outputs={sorted(outputs)}."
        )
    subset_error = _validate_combinational_subset(code, inputs, outputs)
    if subset_error:
        return _semantic_failure(subset_error)
    result = score_functional(
        {"verilog": code, "truth_table": [dict(row) for row in AND2_TRUTH_TABLE]}
    )
    details = dict(result.details or {})
    checked = int(details.get("truth_table_rows_checked") or 0)
    passed = result.score == 1.0 and checked == 4 and not details.get("logic_failures")
    return {
        "schema_version": "and2-semantic-evaluation@1.0.0",
        "passed": passed,
        "interface_match": True,
        "truth_table_exact": passed,
        "truth_table_rows_checked": checked,
        "functional_score": result.score,
        "logic_failures": details.get("logic_failures", []),
        "reason": "Exact AND2 contract passed."
        if passed
        else "Truth table is not exact AND2 0001.",
    }


def validate_failure_record(record: dict[str, Any]) -> list[str]:
    errors = [
        f"missing:{field}" for field in sorted(FAILURE_RECORD_FIELDS - set(record))
    ]
    if record.get("status") not in {"failed", "timeout", "blocked", "rejected"}:
        errors.append("status must be failed, timeout, blocked, or rejected")
    if record.get("schema_version") != "and2-pilot-failure-record@2.0.0":
        errors.append("schema_version must be and2-pilot-failure-record@2.0.0")
    for field_name in ("case_id", "attempt_id", "stage_id", "failure_category"):
        if not isinstance(record.get(field_name), str) or not record.get(field_name):
            errors.append(f"{field_name} must be a non-empty string")
    for field_name in ("paid_cost_usd", "elapsed_seconds"):
        value = record.get(field_name)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            errors.append(f"{field_name} must be a non-negative number")
    provider_calls = record.get("provider_call_count")
    if (
        not isinstance(provider_calls, int)
        or isinstance(provider_calls, bool)
        or provider_calls < 0
    ):
        errors.append("provider_call_count must be a non-negative integer")
    for hash_field in (
        "input_sha256s",
        "pre_input_sha256s",
        "post_input_sha256s",
    ):
        hashes = record.get(hash_field)
        if not isinstance(hashes, dict):
            errors.append(f"{hash_field} must be an object")
            continue
        for key, value in hashes.items():
            if not isinstance(key, str) or not re.fullmatch(
                r"[0-9a-f]{64}", str(value)
            ):
                errors.append(f"{hash_field} values must be lowercase SHA-256 hex")
                break
    hashes_equal = record.get("input_hashes_equal")
    if not isinstance(hashes_equal, bool):
        errors.append("input_hashes_equal must be a boolean")
    elif isinstance(record.get("pre_input_sha256s"), dict) and isinstance(
        record.get("post_input_sha256s"), dict
    ):
        actual_equal = record["pre_input_sha256s"] == record["post_input_sha256s"]
        if hashes_equal != actual_equal:
            errors.append("input_hashes_equal does not match pre/post hashes")
    if (
        isinstance(record.get("input_sha256s"), dict)
        and isinstance(record.get("pre_input_sha256s"), dict)
        and record["input_sha256s"] != record["pre_input_sha256s"]
    ):
        errors.append("input_sha256s must equal pre_input_sha256s")
    if not isinstance(record.get("command"), list) or not all(
        isinstance(part, str) for part in record.get("command", [])
    ):
        errors.append("command must be an array of strings")
    if not isinstance(record.get("runtime_identity"), dict):
        errors.append("runtime_identity must be an object")
    exit_code = record.get("exit_code")
    if exit_code is not None and (
        not isinstance(exit_code, int) or isinstance(exit_code, bool)
    ):
        errors.append("exit_code must be an integer or null")
    if not isinstance(record.get("artifact_inventory"), list):
        errors.append("artifact_inventory must be a list")
    for field_name in ("cleanup_result", "final_disposition"):
        if field_name in record and (
            not isinstance(record[field_name], str) or not record[field_name]
        ):
            errors.append(f"{field_name} must be a non-empty string")
    return errors


def is_loopback_api_base(api_base: str | None) -> bool:
    if not api_base:
        return False
    try:
        hostname = (urlparse(api_base).hostname or "").lower()
    except ValueError:
        return False
    return hostname in {"localhost", "127.0.0.1", "::1"}


def _semantic_failure(reason: str) -> dict[str, Any]:
    return {
        "schema_version": "and2-semantic-evaluation@1.0.0",
        "passed": False,
        "interface_match": False,
        "truth_table_exact": False,
        "truth_table_rows_checked": 0,
        "functional_score": 0.0,
        "logic_failures": [reason],
        "reason": reason,
    }


def _declaration_names(code: str, direction: str) -> set[str]:
    pattern = re.compile(
        rf"\b{direction}\b(?:\s+(?:wire|logic))?\s+(.+?)(?=\b(?:input|output|inout)\b|[;)])",
        re.I | re.S,
    )
    names: set[str] = set()
    for match in pattern.finditer(code):
        declaration = re.sub(r"\[[^\]]+\]", "", match.group(1))
        names.update(re.findall(r"\b[A-Za-z_]\w*\b", declaration))
    return names


def _validate_combinational_subset(
    code: str,
    inputs: set[str],
    outputs: set[str],
) -> str | None:
    declared = (
        inputs
        | outputs
        | _declaration_names(code, "wire")
        | _declaration_names(code, "logic")
    )
    module_match = re.search(
        r"\bmodule\b\s+[A-Za-z_]\w*\s*\(.*?\)\s*;", code, re.I | re.S
    )
    if not module_match:
        return "A complete ANSI-style module header is required."
    body = code[module_match.end() :]
    body = re.sub(r"\bendmodule\b", "", body, flags=re.I)
    body = re.sub(
        r"\b(?:input|output|wire|logic)\b(?:\s+(?:wire|logic))?\s+[^;]+;",
        "",
        body,
        flags=re.I | re.S,
    )
    driven: set[str] = set()
    dependencies: dict[str, set[str]] = {}
    for raw_statement in body.split(";"):
        statement = raw_statement.strip()
        if not statement:
            continue
        assign_match = re.fullmatch(
            r"assign\s+([A-Za-z_]\w*)\s*=\s*(.+)", statement, re.I | re.S
        )
        if assign_match:
            target, expression = assign_match.groups()
            error = _validate_driver(
                target,
                expression,
                declared,
                inputs,
                driven,
                dependencies,
            )
            if error:
                return error
            continue
        gate_match = re.fullmatch(
            r"(and|or|not|nand|nor|xor|xnor)\s+(?:[A-Za-z_]\w*\s*)?\((.*?)\)",
            statement,
            re.I | re.S,
        )
        if gate_match:
            arguments = [part.strip() for part in gate_match.group(2).split(",")]
            if len(arguments) < 2 or not all(
                re.fullmatch(r"[A-Za-z_]\w*", part) for part in arguments
            ):
                return "Primitive gates must use declared scalar signal identifiers."
            error = _validate_driver(
                arguments[0],
                " ".join(arguments[1:]),
                declared,
                inputs,
                driven,
                dependencies,
            )
            if error:
                return error
            continue
        return f"Unsupported Verilog statement: {statement[:80]}"
    if outputs - driven:
        return "Output GFP must have exactly one combinational driver."
    undriven_dependencies = {
        dependency
        for values in dependencies.values()
        for dependency in values
        if dependency not in inputs and dependency not in driven
    }
    if undriven_dependencies:
        return "Expression uses undriven internal signals: " + ", ".join(
            sorted(undriven_dependencies)
        )
    cycle = _find_dependency_cycle(dependencies)
    if cycle:
        return "Combinational dependency cycle is forbidden: " + " -> ".join(cycle)
    return None


def _validate_driver(
    target: str,
    expression: str,
    declared: set[str],
    inputs: set[str],
    driven: set[str],
    dependencies: dict[str, set[str]],
) -> str | None:
    if target not in declared or target in inputs:
        return f"Driver target must be a declared non-input signal: {target}"
    if target in driven:
        return f"Signal has multiple drivers: {target}"
    if re.search(r"[^A-Za-z0-9_'()~!&|^\s]", expression):
        return "Expression contains unsupported syntax."
    identifiers = set(re.findall(r"\b[A-Za-z_]\w*\b", expression))
    unresolved = identifiers - declared
    if unresolved:
        return "Expression uses unresolved signals: " + ", ".join(sorted(unresolved))
    driven.add(target)
    dependencies[target] = identifiers
    return None


def _find_dependency_cycle(dependencies: dict[str, set[str]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(signal: str) -> list[str]:
        if signal in visiting:
            start = stack.index(signal)
            return stack[start:] + [signal]
        if signal in visited:
            return []
        visiting.add(signal)
        stack.append(signal)
        for dependency in sorted(dependencies.get(signal, set())):
            if dependency not in dependencies:
                continue
            cycle = visit(dependency)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(signal)
        visited.add(signal)
        return []

    for signal in sorted(dependencies):
        cycle = visit(signal)
        if cycle:
            return cycle
    return []
