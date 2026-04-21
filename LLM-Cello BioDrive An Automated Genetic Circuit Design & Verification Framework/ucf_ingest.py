"""
UCF (Cello User Constraint File) ingestion script.

Goal:
- Read a Cello UCF JSON file (e.g., Eco1C1G1T1.UCF.json).
- Extract three core component categories:
  1) sensors (inputs)
  2) gates (logic gates such as NOR/NOT)
  3) output_devices (outputs)
- Convert each component into a highly structured natural-language description string
  suitable for embeddings / RAG indexing.

Output:
- A Python list[str] containing all formatted descriptions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _safe_json_dumps(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(obj)


def _pick_first(d: dict[str, Any], keys: Iterable[str]) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, "", [], {}):
            return d[k]
    return None


def _get_collection(entry: dict[str, Any]) -> str:
    c = _pick_first(entry, ("collection", "type", "category", "class"))
    return str(c).strip() if c is not None else ""


def _is_sensor(collection: str) -> bool:
    c = collection.lower()
    return ("sensor" in c) or ("input" in c and "output" not in c)


def _is_gate(collection: str) -> bool:
    c = collection.lower()
    return "gate" in c


def _is_output_device(collection: str) -> bool:
    c = collection.lower()
    return ("output" in c and "device" in c) or (c == "output_devices") or (c == "output_device")


def _extract_response_function(entry: dict[str, Any]) -> dict[str, Any] | None:
    """
    UCF response functions appear in a few common shapes. We normalize them as:
    { "equation": "...", "parameters": {...} } when present.
    """
    rf = _pick_first(entry, ("response_function", "responseFunction", "response", "transfer_function"))
    if rf is None:
        return None

    # Sometimes it's already a dict with equation/parameters.
    if isinstance(rf, dict):
        equation = _pick_first(rf, ("equation", "model", "name", "type"))
        params = _pick_first(rf, ("parameters", "params", "parameter", "coefficients"))
        out: dict[str, Any] = {}
        if equation is not None:
            out["equation"] = equation
        if params is not None:
            out["parameters"] = params
        return out or rf

    # Sometimes it's a string or something else.
    return {"raw": rf}


def _extract_toxicity_and_burden(entry: dict[str, Any]) -> dict[str, Any]:
    tox = _pick_first(entry, ("toxicity", "toxic", "tox"))
    burden = _pick_first(entry, ("metabolic_burden", "metabolicBurden", "burden", "load", "metabolic_load"))
    out: dict[str, Any] = {}
    if tox is not None:
        out["toxicity"] = tox
    if burden is not None:
        out["metabolic_burden"] = burden
    return out


def _format_component_description(entry: dict[str, Any]) -> str:
    collection = _get_collection(entry)
    name = _pick_first(entry, ("name", "id", "identifier", "part_name", "device_name"))
    organism = _pick_first(entry, ("organism", "host", "chassis", "species"))

    gate_type = _pick_first(entry, ("gate_type", "gateType", "logic", "logic_type"))
    if gate_type is None and _is_gate(collection):
        gate_type = _pick_first(entry, ("type", "family", "function"))

    sensor_type = _pick_first(entry, ("sensor_type", "sensorType", "input_type", "signal", "inducer"))
    output_type = _pick_first(entry, ("output_type", "outputType", "reporter", "protein", "fluorophore"))

    # Some UCF objects include useful context like "description" or "notes".
    short_desc = _pick_first(entry, ("description", "desc", "notes", "note"))

    rf = _extract_response_function(entry)
    tox = _extract_toxicity_and_burden(entry)

    # Normalize basic labels
    name_s = str(name).strip() if name is not None else "（未命名）"
    collection_s = collection if collection else "（未知 collection）"

    # Category-specific phrasing
    if _is_gate(collection_s):
        role = "邏輯閘（gate）"
        function = f"這是一個 {gate_type} 邏輯閘。" if gate_type else "這是一個邏輯閘。"
    elif _is_output_device(collection_s):
        role = "輸出元件（output device）"
        function = f"這是一個輸出元件，輸出類型為 {output_type}。" if output_type else "這是一個輸出元件。"
    elif _is_sensor(collection_s):
        role = "感測器／輸入（sensor/input）"
        function = f"這是一個感測器／輸入元件，感測訊號為 {sensor_type}。" if sensor_type else "這是一個感測器／輸入元件。"
    else:
        role = "元件（component）"
        function = "這是一個 UCF 元件。"

    organism_s = f"{organism}" if organism is not None else "未指定"

    pieces: list[str] = [
        f"元件名稱：{name_s}。",
        f"類型：{collection_s}（{role}）。",
        f"功能描述：{function}適用宿主/生物體：{organism_s}。",
    ]

    if short_desc:
        pieces.append(f"附註：{str(short_desc).strip()}。")

    if rf:
        # Try to highlight common RF parameters without assuming schema.
        eq = rf.get("equation") if isinstance(rf, dict) else None
        params = rf.get("parameters") if isinstance(rf, dict) else None
        if eq is not None and params is not None:
            pieces.append(f"反應函數：equation={eq}；parameters={_safe_json_dumps(params)}。")
        elif isinstance(rf, dict) and "raw" in rf:
            pieces.append(f"反應函數：{_safe_json_dumps(rf['raw'])}。")
        else:
            pieces.append(f"反應函數：{_safe_json_dumps(rf)}。")

    if tox:
        if "toxicity" in tox:
            pieces.append(f"毒性：{_safe_json_dumps(tox['toxicity'])}。")
        if "metabolic_burden" in tox:
            pieces.append(f"代謝負擔：{_safe_json_dumps(tox['metabolic_burden'])}。")

    return " ".join(pieces).strip()


def parse_ucf_to_descriptions(ucf_json_path: str | Path) -> list[str]:
    """
    Read a UCF JSON file and return a list[str] of structured natural-language descriptions.

    Notes on UCF structure:
    - Many Cello UCF files are a JSON array of objects, each with a "collection" field.
    - Some variants wrap the array under a top-level key; we handle common wrappers.
    """
    path = Path(ucf_json_path)
    if not path.exists():
        raise FileNotFoundError(f"UCF JSON not found: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {path}: {e}") from e

    # Accept several possible top-level shapes
    entries: list[dict[str, Any]] = []
    if isinstance(raw, list):
        entries = [x for x in raw if isinstance(x, dict)]
    elif isinstance(raw, dict):
        # Common wrappers observed in JSON exports
        candidate = _pick_first(raw, ("ucf", "UCF", "data", "entries", "objects", "components"))
        if isinstance(candidate, list):
            entries = [x for x in candidate if isinstance(x, dict)]
        else:
            # Sometimes a dict-of-dicts
            items = []
            for v in raw.values():
                items.extend(_as_list(v))
            entries = [x for x in items if isinstance(x, dict)]
    else:
        raise ValueError(f"Unexpected JSON top-level type: {type(raw)}")

    # Extract only the three requested categories
    selected: list[dict[str, Any]] = []
    for e in entries:
        c = _get_collection(e)
        if _is_sensor(c) or _is_gate(c) or _is_output_device(c):
            selected.append(e)

    # Format into embedding-friendly strings
    descriptions: list[str] = []
    for e in selected:
        try:
            descriptions.append(_format_component_description(e))
        except Exception:
            # Do not fail the whole ingestion due to one malformed entry.
            fallback_name = _pick_first(e, ("name", "id", "identifier"))
            descriptions.append(
                "元件名稱："
                + (str(fallback_name) if fallback_name is not None else "（未命名）")
                + "。"
                + "類型："
                + (_get_collection(e) or "（未知 collection）")
                + "。"
                + "功能描述：此元件解析失敗，以下保留原始欄位以供除錯："
                + _safe_json_dumps(e)
            )

    return descriptions


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse a Cello UCF JSON into embedding-ready descriptions.")
    parser.add_argument("ucf_json", help="Path to the UCF JSON file (e.g., Eco1C1G1T1.UCF.json).")
    args = parser.parse_args()

    try:
        out = parse_ucf_to_descriptions(args.ucf_json)
    except Exception as e:
        # CLI-friendly error output
        raise SystemExit(f"Error: {e}") from e

    # Requirement: output should be a Python List containing all strings.
    print(out)


if __name__ == "__main__":
    main()

