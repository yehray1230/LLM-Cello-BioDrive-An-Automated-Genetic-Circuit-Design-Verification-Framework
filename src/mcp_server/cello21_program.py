from __future__ import annotations

import json
import math
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generic, Literal, TypeVar


Action = Literal["provider", "mapping"]
T = TypeVar("T")


class Cello21ProgramBlocked(RuntimeError):
    """Raised before an unauthorized or over-budget external action."""


@dataclass(frozen=True)
class ProviderCallResult(Generic[T]):
    value: T
    request_id: str
    model: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    actual_cost_usd: float


def _non_negative_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Cello21ProgramBlocked(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise Cello21ProgramBlocked(f"{label} must be finite and non-negative")
    return number


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise Cello21ProgramBlocked(f"{label} must be a positive integer")
    return value


@dataclass(frozen=True)
class Cello21ProgramPolicy:
    program_id: str
    experiment_id: str
    active_experiment_id: str | None
    execution_authorized: bool
    network_authorized: bool
    paid_api_authorized: bool
    cello_mapping_authorized: bool
    retry_authorized: bool
    maximum_paid_calls: int
    maximum_mapping_runs: int
    paid_cost_cap_usd: float
    paid_cost_cap_twd: float
    budget_exchange_rate_twd_per_usd: float
    per_call_reservation_usd: float

    @classmethod
    def from_payloads(
        cls,
        *,
        state: dict[str, Any],
        budget: dict[str, Any],
        experiment_id: str,
        maximum_mapping_runs: int,
    ) -> Cello21ProgramPolicy:
        reservation = budget.get("per_call_reservation")
        if not isinstance(reservation, dict):
            raise Cello21ProgramBlocked("per_call_reservation is required")
        return cls(
            program_id=str(state.get("program_id") or "").strip(),
            experiment_id=str(experiment_id or "").strip(),
            active_experiment_id=(
                str(state["active_experiment_id"]).strip()
                if state.get("active_experiment_id") is not None
                else None
            ),
            execution_authorized=state.get("execution_authorized") is True,
            network_authorized=state.get("network_authorized") is True,
            paid_api_authorized=state.get("paid_api_authorized") is True,
            cello_mapping_authorized=(
                state.get("cello_mapping_authorized") is True
            ),
            retry_authorized=state.get("retry_authorized") is True,
            maximum_paid_calls=_positive_int(
                budget.get("maximum_paid_calls"), "maximum_paid_calls"
            ),
            maximum_mapping_runs=_positive_int(
                maximum_mapping_runs, "maximum_mapping_runs"
            ),
            paid_cost_cap_usd=_non_negative_number(
                state.get("paid_cost_cap_usd", 0.0), "paid_cost_cap_usd"
            ),
            paid_cost_cap_twd=_non_negative_number(
                state.get("paid_cost_cap_twd", 0.0), "paid_cost_cap_twd"
            ),
            budget_exchange_rate_twd_per_usd=_non_negative_number(
                budget.get("budget_exchange_rate_twd_per_usd"),
                "budget_exchange_rate_twd_per_usd",
            ),
            per_call_reservation_usd=_non_negative_number(
                reservation.get("reserved_total_cost_usd"),
                "reserved_total_cost_usd",
            ),
        )

    def require(self, action: Action) -> None:
        if not self.program_id or not self.experiment_id:
            raise Cello21ProgramBlocked("program and experiment IDs are required")
        if self.active_experiment_id != self.experiment_id:
            raise Cello21ProgramBlocked(
                "active experiment must exactly match the requested experiment"
            )
        if not self.execution_authorized:
            raise Cello21ProgramBlocked("execution is not authorized")
        if self.retry_authorized:
            raise Cello21ProgramBlocked("automatic retry must remain disabled")
        if action == "provider":
            if not self.network_authorized or not self.paid_api_authorized:
                raise Cello21ProgramBlocked(
                    "provider execution requires network and paid API authorization"
                )
            if self.paid_cost_cap_usd <= 0 or self.paid_cost_cap_twd <= 0:
                raise Cello21ProgramBlocked(
                    "provider execution requires positive USD and TWD caps"
                )
            if (
                self.per_call_reservation_usd <= 0
                or self.budget_exchange_rate_twd_per_usd <= 0
            ):
                raise Cello21ProgramBlocked(
                    "provider execution requires positive frozen reservation and exchange rate"
                )
        elif not self.cello_mapping_authorized:
            raise Cello21ProgramBlocked("Cello mapping is not authorized")


class Cello21ProgramRunner:
    """One-attempt runner with an append-only reservation/action ledger."""

    def __init__(self, policy: Cello21ProgramPolicy, ledger_path: Path):
        self.policy = policy
        self.ledger_path = ledger_path

    def preflight(self) -> dict[str, Any]:
        return {
            "program_id": self.policy.program_id,
            "experiment_id": self.policy.experiment_id,
            "active_experiment_id": self.policy.active_experiment_id,
            "execution_authorized": self.policy.execution_authorized,
            "network_authorized": self.policy.network_authorized,
            "paid_api_authorized": self.policy.paid_api_authorized,
            "cello_mapping_authorized": self.policy.cello_mapping_authorized,
            "retry_authorized": self.policy.retry_authorized,
            "ledger_present": self.ledger_path.exists(),
            "ledger": self._ledger_summary(),
        }

    def run_provider_step(
        self, stage: str, operation: Callable[[], ProviderCallResult[T]]
    ) -> T:
        self.policy.require("provider")
        attempt_id = uuid.uuid4().hex
        with self._ledger_lock():
            summary = self._ledger_summary()
            self._require_no_incomplete_attempt(summary)
            next_calls = summary["provider_reservations"] + 1
            next_usd = next_calls * self.policy.per_call_reservation_usd
            next_twd = next_usd * self.policy.budget_exchange_rate_twd_per_usd
            if next_calls > self.policy.maximum_paid_calls:
                raise Cello21ProgramBlocked("provider call cap would be exceeded")
            if next_usd > self.policy.paid_cost_cap_usd:
                raise Cello21ProgramBlocked("USD cost cap would be exceeded")
            if next_twd > self.policy.paid_cost_cap_twd:
                raise Cello21ProgramBlocked("TWD cost cap would be exceeded")
            self._append(
                {
                    "event": "provider_reserved",
                    "attempt_id": attempt_id,
                    "stage": stage,
                    "reserved_cost_usd": self.policy.per_call_reservation_usd,
                    "reserved_total_usd": next_usd,
                    "reserved_total_twd": next_twd,
                }
            )
        try:
            result = operation()
        except Exception as exc:
            with self._ledger_lock():
                self._append(
                    {
                        "event": "provider_finished",
                        "attempt_id": attempt_id,
                        "stage": stage,
                        "status": "failed_no_retry",
                        "error_type": type(exc).__name__,
                    }
                )
            raise
        actual_cost_usd = _non_negative_number(
            result.actual_cost_usd, "actual_cost_usd"
        )
        actual_cost_twd = (
            actual_cost_usd * self.policy.budget_exchange_rate_twd_per_usd
        )
        with self._ledger_lock():
            self._append(
                {
                    "event": "provider_finished",
                    "attempt_id": attempt_id,
                    "stage": stage,
                    "status": "completed",
                    "provider_request_id": _required_text(
                        result.request_id, "provider_request_id"
                    ),
                    "model": _required_text(result.model, "model"),
                    "input_tokens": _positive_or_zero_int(
                        result.input_tokens, "input_tokens"
                    ),
                    "cached_input_tokens": _positive_or_zero_int(
                        result.cached_input_tokens, "cached_input_tokens"
                    ),
                    "output_tokens": _positive_or_zero_int(
                        result.output_tokens, "output_tokens"
                    ),
                    "actual_cost_usd": actual_cost_usd,
                    "actual_cost_twd": actual_cost_twd,
                    "reservation_exceeded": (
                        actual_cost_usd > self.policy.per_call_reservation_usd
                    ),
                }
            )
        if actual_cost_usd > self.policy.per_call_reservation_usd:
            raise Cello21ProgramBlocked(
                "observed provider cost exceeded the frozen reservation; stop before next call"
            )
        return result.value

    def run_mapping_step(self, stage: str, operation: Callable[[], T]) -> T:
        self.policy.require("mapping")
        attempt_id = uuid.uuid4().hex
        with self._ledger_lock():
            summary = self._ledger_summary()
            self._require_no_incomplete_attempt(summary)
            if summary["mapping_reservations"] >= self.policy.maximum_mapping_runs:
                raise Cello21ProgramBlocked("Cello mapping run cap would be exceeded")
            self._append(
                {
                    "event": "mapping_reserved",
                    "attempt_id": attempt_id,
                    "stage": stage,
                }
            )
        try:
            result = operation()
        except Exception as exc:
            with self._ledger_lock():
                self._append(
                    {
                        "event": "mapping_finished",
                        "attempt_id": attempt_id,
                        "stage": stage,
                        "status": "failed_no_retry",
                        "error_type": type(exc).__name__,
                    }
                )
            raise
        with self._ledger_lock():
            self._append(
                {
                    "event": "mapping_finished",
                    "attempt_id": attempt_id,
                    "stage": stage,
                    "status": "completed",
                }
            )
        return result

    @contextmanager
    def _ledger_lock(self):
        lock_path = self.ledger_path.with_name(self.ledger_path.name + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise Cello21ProgramBlocked(
                "ledger is locked by another runner; automatic waiting is disabled"
            ) from exc
        try:
            os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
            yield
        finally:
            os.close(descriptor)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    def _append(self, event: dict[str, Any]) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "cello21.program-ledger.v1",
            "program_id": self.policy.program_id,
            "experiment_id": self.policy.experiment_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            **event,
        }
        with self.ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def _ledger_summary(self) -> dict[str, int]:
        provider_reservations = 0
        mapping_reservations = 0
        attempts: dict[str, tuple[str, str, bool]] = {}
        if self.ledger_path.exists():
            for line_number, line in enumerate(
                self.ledger_path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise Cello21ProgramBlocked(
                        f"ledger line {line_number} is invalid JSON"
                    ) from exc
                if event.get("schema_version") != "cello21.program-ledger.v1":
                    raise Cello21ProgramBlocked(
                        f"ledger line {line_number} has an unsupported schema version"
                    )
                if (
                    event.get("program_id") != self.policy.program_id
                    or event.get("experiment_id") != self.policy.experiment_id
                ):
                    raise Cello21ProgramBlocked(
                        "ledger contains a different program or experiment"
                    )
                event_name = event.get("event")
                allowed_events = {
                    "provider_reserved",
                    "provider_finished",
                    "mapping_reserved",
                    "mapping_finished",
                }
                if event_name not in allowed_events:
                    raise Cello21ProgramBlocked(
                        f"ledger line {line_number} has an unsupported event"
                    )
                attempt_id = _required_text(event.get("attempt_id"), "attempt_id")
                stage = _required_text(event.get("stage"), "stage")
                _required_text(event.get("timestamp_utc"), "timestamp_utc")
                action, phase = str(event_name).split("_", 1)
                if phase == "reserved":
                    if attempt_id in attempts:
                        raise Cello21ProgramBlocked(
                            f"ledger line {line_number} duplicates an attempt ID"
                        )
                    if action == "provider":
                        _non_negative_number(
                            event.get("reserved_cost_usd"), "reserved_cost_usd"
                        )
                        _non_negative_number(
                            event.get("reserved_total_usd"), "reserved_total_usd"
                        )
                        _non_negative_number(
                            event.get("reserved_total_twd"), "reserved_total_twd"
                        )
                        provider_reservations += 1
                    else:
                        mapping_reservations += 1
                    attempts[attempt_id] = (action, stage, False)
                    continue
                reservation = attempts.get(attempt_id)
                if reservation is None:
                    raise Cello21ProgramBlocked(
                        f"ledger line {line_number} finishes an unreserved attempt"
                    )
                reserved_action, reserved_stage, finished = reservation
                if finished:
                    raise Cello21ProgramBlocked(
                        f"ledger line {line_number} finishes an attempt more than once"
                    )
                if reserved_action != action or reserved_stage != stage:
                    raise Cello21ProgramBlocked(
                        f"ledger line {line_number} does not match its reservation"
                    )
                status = event.get("status")
                if status not in {"completed", "failed_no_retry"}:
                    raise Cello21ProgramBlocked(
                        f"ledger line {line_number} has an unsupported completion status"
                    )
                if status == "failed_no_retry":
                    _required_text(event.get("error_type"), "error_type")
                elif action == "provider":
                    _required_text(event.get("provider_request_id"), "provider_request_id")
                    _required_text(event.get("model"), "model")
                    _positive_or_zero_int(event.get("input_tokens"), "input_tokens")
                    _positive_or_zero_int(
                        event.get("cached_input_tokens"), "cached_input_tokens"
                    )
                    _positive_or_zero_int(event.get("output_tokens"), "output_tokens")
                    _non_negative_number(event.get("actual_cost_usd"), "actual_cost_usd")
                    _non_negative_number(event.get("actual_cost_twd"), "actual_cost_twd")
                    if not isinstance(event.get("reservation_exceeded"), bool):
                        raise Cello21ProgramBlocked(
                            "reservation_exceeded must be boolean"
                        )
                attempts[attempt_id] = (action, stage, True)
        incomplete_attempts = sum(1 for _, _, finished in attempts.values() if not finished)
        return {
            "provider_reservations": provider_reservations,
            "mapping_reservations": mapping_reservations,
            "incomplete_attempts": incomplete_attempts,
        }

    @staticmethod
    def _require_no_incomplete_attempt(summary: dict[str, int]) -> None:
        if summary["incomplete_attempts"]:
            raise Cello21ProgramBlocked(
                "ledger contains an incomplete attempt; retry and concurrency are disabled"
            )


def _positive_or_zero_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Cello21ProgramBlocked(f"{label} must be a non-negative integer")
    return value


def _required_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise Cello21ProgramBlocked(f"{label} must be non-empty text")
    return text
