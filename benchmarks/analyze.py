"""Summarize Pi JSONL usage and score the benchmark decision matrix.

The input transcript is Pi's JSONL event stream.  A stage starts at a
``custom_message`` whose ``customType`` is
``agent-coordination.message-delivery`` and whose JSON ``content`` contains a
request message.  It ends at an assistant ``agent_message`` tool call whose
``arguments.operation`` is ``answer``.  Only timestamps, provider usage, and
small numeric diagnostics are emitted; message content (including tool output
and encrypted reasoning) is deliberately never copied.

The manifest accepted by :func:`evaluate_manifest` is either a JSON list or an
object with a ``modes`` list.  Each mode has ``name``, ``transcript`` (or
``transcriptPath``), ``correctness`` and ``recovery`` (each either a fraction,
``{"passed": n, "total": n}``, or a list of booleans), and ``simplicity``
(a fixed score from 0 to 5).  A transcript supplies measured ``usage`` and
``activeElapsedSeconds``; these may instead be supplied directly for already
sanitized records.  ``expectedStageCount`` and ``stageCount`` can mark an
incomplete matched workload ineligible.  Provider ``totalTokens`` is summed
once and is never recomputed by adding reasoning or cache fields.  Baseline
results expose an empirical subtotal out of 90 (correctness, tokens, latency,
and recovery) separately from the judgment-inclusive score out of 100.

CLI::

    python benchmarks/analyze.py summarize PARTICIPANT.jsonl
    python benchmarks/analyze.py evaluate MANIFEST.json

Both commands print sanitized JSON.  Raw transcript data must remain outside
the generated benchmark artifacts.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
import json
import math
from pathlib import Path
from typing import Any


USAGE_FIELDS = ("input", "output", "cacheRead", "cacheWrite", "totalTokens", "reasoning")
BASELINE_WEIGHTS = {"correctness": 35, "tokens": 25, "latency": 10, "recovery": 20, "simplicity": 10}
TOKEN_HEAVY_WEIGHTS = {"correctness": 25, "tokens": 40, "latency": 10, "recovery": 20, "simplicity": 5}
RELIABILITY_HEAVY_WEIGHTS = {"correctness": 40, "tokens": 15, "latency": 5, "recovery": 30, "simplicity": 10}
WEIGHT_SCENARIOS = {
    "baseline": BASELINE_WEIGHTS,
    "token-heavy": TOKEN_HEAVY_WEIGHTS,
    "reliability-heavy": RELIABILITY_HEAVY_WEIGHTS,
}


def _number(value: Any, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


def _add(left: int | float, right: int | float) -> int | float:
    value = left + right
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _empty_usage() -> dict[str, int | float]:
    return {field: 0 for field in USAGE_FIELDS} | {"costTotal": 0}


def _usage(raw: Any, *, line_number: int) -> dict[str, int | float]:
    if not isinstance(raw, Mapping) or "totalTokens" not in raw:
        raise ValueError(f"assistant message {line_number} is missing usage")
    result = _empty_usage()
    for field in USAGE_FIELDS:
        if field in raw:
            result[field] = _number(raw[field], f"usage.{field}")
    if result["totalTokens"] <= 0:
        raise ValueError(f"assistant message {line_number} has zero usage.totalTokens")
    cost = raw.get("cost", {})
    if isinstance(cost, Mapping) and "total" in cost:
        result["costTotal"] = _number(cost["total"], "usage.cost.total")
    return result


def _sum_usage(target: dict[str, int | float], addition: Mapping[str, Any]) -> None:
    for field in (*USAGE_FIELDS, "costTotal"):
        target[field] = _add(target[field], addition.get(field, 0))


def _public_usage(value: Mapping[str, Any]) -> dict[str, Any]:
    """Expose provider-shaped usage while keeping the accumulator private."""
    result = {field: value.get(field, 0) for field in USAGE_FIELDS}
    result["cost"] = {"total": value.get("costTotal", 0)}
    return result


def _timestamp(raw: Any) -> datetime:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        # Pi timestamps are normally epoch milliseconds; accept seconds too
        # for small synthetic transcripts.
        seconds = raw / 1000 if raw > 100_000_000_000 else raw
        return datetime.fromtimestamp(seconds).astimezone()
    if isinstance(raw, str):
        text = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed
    raise ValueError("stage timestamps must be ISO strings or epoch numbers")


def _content(record: Mapping[str, Any]) -> Any:
    message = record.get("message")
    return message.get("content", []) if isinstance(message, Mapping) else record.get("content", [])


def _items(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
    elif isinstance(value, list):
        yield from (item for item in value if isinstance(item, Mapping))


def _arguments(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, Mapping) else {}
    return {}


def _is_answer_call(record: Mapping[str, Any]) -> bool:
    for item in _items(_content(record)):
        if item.get("type") == "toolCall" and item.get("name") == "agent_message":
            if _arguments(item.get("arguments")).get("operation") == "answer":
                return True
    return False


def _stage_request(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if record.get("type") != "custom_message" or record.get("customType") != "agent-coordination.message-delivery":
        return None
    content = record.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return None
    if not isinstance(content, Mapping) or not isinstance(content.get("messages"), list):
        return None
    for message in content["messages"]:
        if isinstance(message, Mapping) and message.get("kind") == "request":
            return message
    return None


def _tool_result_chars(record: Mapping[str, Any]) -> int:
    nested_message = record.get("message")
    is_nested_tool_result = isinstance(nested_message, Mapping) and nested_message.get("role") == "toolResult"
    if record.get("type") != "toolResult" and not is_nested_tool_result:
        return 0
    content = nested_message.get("content") if is_nested_tool_result else record.get("content")
    if isinstance(content, str):
        return len(content)
    return sum(len(item.get("text", "")) for item in _items(content) if isinstance(item.get("text", ""), str))


def _exec_code_chars(record: Mapping[str, Any]) -> int:
    total = 0
    for item in _items(_content(record)):
        if item.get("type") == "toolCall" and item.get("name") == "exec":
            code = _arguments(item.get("arguments")).get("code", "")
            if isinstance(code, str):
                total += len(code)
    return total


def _record_message(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    message = record.get("message")
    if isinstance(message, Mapping):
        return message
    return record if "role" in record else None


def _read_records(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on transcript line {line_number}") from exc
            if isinstance(value, dict):
                value["_lineNumber"] = line_number
                records.append(value)
    return records


def summarize_transcript(path: str | Path, *, participant: str | None = None) -> dict[str, Any]:
    records = _read_records(path)
    aggregate = _empty_usage()
    sanitized_messages: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None
    result_chars = 0
    exec_chars = 0
    assistant_calls = 0

    for record in records:
        request = _stage_request(record)
        if request is not None:
            if active is not None:
                raise ValueError("transcript contains overlapping stages")
            started_at = record.get("timestamp")
            if started_at is None:
                raise ValueError("stage start is missing timestamp")
            active = {
                "requestMessageId": request.get("requestMessageId"),
                "title": request.get("title"),
                "startedAt": started_at,
                "_start": _timestamp(started_at),
                "_usage": _empty_usage(),
            }
            continue

        result_chars += _tool_result_chars(record)
        exec_chars += _exec_code_chars(record)
        message = _record_message(record)
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            continue

        line_number = int(record.get("_lineNumber", 0))
        current_usage = _usage(message.get("usage"), line_number=line_number)
        assistant_calls += 1
        _sum_usage(aggregate, current_usage)
        sanitized_messages.append({"timestamp": record.get("timestamp"), "usage": _public_usage(current_usage)})
        if active is not None:
            _sum_usage(active["_usage"], current_usage)
            if _is_answer_call(record):
                ended_at = record.get("timestamp")
                if ended_at is None:
                    raise ValueError("stage end is missing timestamp")
                elapsed = (_timestamp(ended_at) - active["_start"]).total_seconds()
                if elapsed < 0:
                    raise ValueError("stage end precedes stage start")
                stage = {
                    "requestMessageId": active["requestMessageId"],
                    "title": active["title"],
                    "startedAt": active["startedAt"],
                    "endedAt": ended_at,
                    "activeElapsedSeconds": elapsed,
                    "usage": _public_usage(active["_usage"]),
                }
                stages.append(stage)
                active = None

    if active is not None:
        raise ValueError(f"stage {active['requestMessageId']!r} has no answer")
    if assistant_calls == 0:
        raise ValueError("transcript contains no assistant usage")
    return {
        "participant": participant or Path(path).stem,
        "transcript": str(path),
        "messages": sanitized_messages,
        "usage": _public_usage(aggregate),
        "assistantCalls": assistant_calls,
        "diagnostics": {"toolResultTextChars": result_chars, "execCodeChars": exec_chars},
        "stages": stages,
        "stageTiming": {"activeElapsedSeconds": sum(stage["activeElapsedSeconds"] for stage in stages)},
    }


analyze_transcript = summarize_transcript


def _fraction(value: Any, label: str) -> float:
    if isinstance(value, Mapping):
        if "passed" not in value or "total" not in value:
            raise ValueError(f"{label} must contain passed and total")
        passed = _number(value["passed"], f"{label}.passed")
        total = _number(value["total"], f"{label}.total")
        if total <= 0 or passed < 0 or passed > total:
            raise ValueError(f"invalid {label} fraction")
        return passed / total
    if isinstance(value, list):
        if not value:
            raise ValueError(f"{label} checks cannot be empty")
        return sum(bool(item) for item in value) / len(value)
    fraction = float(_number(value, label))
    if not 0 <= fraction <= 1:
        raise ValueError(f"{label} fraction must be between 0 and 1")
    return fraction


def _mode_fraction(mode: Mapping[str, Any], name: str) -> float:
    aliases = [
        name,
        f"{name}Fraction",
        f"{name}Checks",
        f"{name}_fraction",
        f"{name}_checks",
        f"ordinary{name.title()}Checks" if name == "correctness" else f"{name.title()}Checks",
        f"ordinary{name.title()}Fraction" if name == "correctness" else f"{name.title()}Fraction",
        f"ordinary_{name}" if name == "correctness" else f"recovery_{name}",
    ]
    if name == "correctness":
        aliases.extend(("ordinary", "ordinaryChecks", "ordinary_correctness_checks", "ordinary_correctness_fraction"))
    else:
        aliases.extend(("recoveryCorrectness", "recovery_correctness", "recovery_checks", "recovery_fraction"))
    for key in aliases:
        if key in mode:
            return _fraction(mode[key], name)
    raise ValueError(f"mode is missing {name}")


def _measured_tokens(mode: Mapping[str, Any]) -> int | float:
    usage = mode.get("usage")
    if isinstance(usage, Mapping):
        raw = usage.get("totalTokens")
    else:
        raw = mode.get("totalTokens")
    value = _number(raw, "totalTokens")
    if value <= 0:
        raise ValueError("totalTokens must be greater than zero")
    return value


def _measured_latency(mode: Mapping[str, Any]) -> int | float:
    raw = mode.get("activeElapsedSeconds")
    if raw is None and isinstance(mode.get("stageTiming"), Mapping):
        raw = mode["stageTiming"].get("activeElapsedSeconds")
    value = _number(raw, "activeElapsedSeconds")
    if value <= 0:
        raise ValueError("activeElapsedSeconds must be greater than zero")
    return value


def _mode_record(mode: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(mode)
    result["correctnessFraction"] = _mode_fraction(mode, "correctness")
    result["recoveryFraction"] = _mode_fraction(mode, "recovery")
    simplicity = mode.get("simplicity", mode.get("simplicityScore"))
    simplicity = _number(simplicity, "simplicity")
    if not 0 <= simplicity <= 5:
        raise ValueError("simplicity must be between 0 and 5")
    result["simplicity"] = simplicity
    result["totalTokens"] = _measured_tokens(mode)
    result["activeElapsedSeconds"] = _measured_latency(mode)
    expected_stages = mode.get("expectedStageCount", mode.get("expectedStageRequests"))
    actual_stages = mode.get("stageCount")
    if expected_stages is not None or actual_stages is not None:
        if expected_stages is None or actual_stages is None:
            result["matchedWorkloadComplete"] = False
        else:
            result["matchedWorkloadComplete"] = actual_stages == expected_stages
    else:
        result["matchedWorkloadComplete"] = mode.get("matchedWorkloadComplete", True) is True
    return result


def _score_modes_once(modes: Sequence[Mapping[str, Any]], selected_weights: Mapping[str, int]) -> dict[str, Any]:
    if not modes:
        raise ValueError("at least one mode is required")
    prepared = [_mode_record(mode) for mode in modes]
    names = [str(mode.get("name", mode.get("mode", ""))) for mode in prepared]
    if any(not name for name in names) or len(set(names)) != len(names):
        raise ValueError("mode names must be present and unique")
    complete_modes = [mode for mode in prepared if mode["matchedWorkloadComplete"]]
    token_min = min((mode["totalTokens"] for mode in complete_modes), default=None)
    latency_min = min((mode["activeElapsedSeconds"] for mode in complete_modes), default=None)
    selected_weights = dict(selected_weights)
    if set(selected_weights) != set(BASELINE_WEIGHTS) or sum(selected_weights.values()) != 100:
        raise ValueError("weights must contain the five criteria and sum to 100")

    scored: list[dict[str, Any]] = []
    for mode in prepared:
        if not mode["matchedWorkloadComplete"] or token_min is None or latency_min is None:
            token_score = 0
            latency_score = 0
        else:
            token_score = 5 * token_min / mode["totalTokens"]
            latency_score = 5 * latency_min / mode["activeElapsedSeconds"]
        scores = {
            "correctness": 5 * mode["correctnessFraction"],
            "tokens": token_score,
            "latency": latency_score,
            "recovery": 5 * mode["recoveryFraction"],
            "simplicity": mode["simplicity"],
        }
        overall = sum(selected_weights[key] * scores[key] for key in selected_weights) / 5
        # Keep this comparable across sensitivity scenarios: the /90 subtotal
        # always uses the predeclared baseline's four measured criteria.
        empirical_subtotal = sum(BASELINE_WEIGHTS[key] * scores[key] for key in ("correctness", "tokens", "latency", "recovery")) / 5
        scored.append({**mode, "scores": scores, "overall": overall,
                       "empiricalSubtotal": empirical_subtotal,
                       "eligible": mode["correctnessFraction"] == 1 and mode["recoveryFraction"] == 1 and mode["matchedWorkloadComplete"]})

    score_ordering = sorted(scored, key=lambda mode: (-mode["overall"], names.index(str(mode["name"]))))
    # Keep a vetoed mode visible in the score ordering, but never let it win
    # the reported recommendation/ranking over a complete eligible mode.
    ordering = sorted(scored, key=lambda mode: (not mode["eligible"], -mode["overall"], names.index(str(mode["name"]))))
    ranking = [str(mode["name"]) for mode in ordering]
    score_ranking = [str(mode["name"]) for mode in score_ordering]
    return {
        "weights": selected_weights,
        "measured": {"smallestTotalTokens": token_min, "fastestActiveElapsedSeconds": latency_min},
        "modes": scored,
        "rankings": {"custom": ranking},
        "scoreRankings": {"custom": score_ranking},
        "recommendation": {"custom": next((str(mode["name"]) for mode in ordering if mode["eligible"]), None)},
    }


def score_modes(modes: Sequence[Mapping[str, Any]], *, weights: Mapping[str, int] | None = None) -> dict[str, Any]:
    """Score modes, including sensitivity rankings unless custom weights are supplied."""
    if weights is not None:
        return _score_modes_once(modes, weights)
    results = {scenario: _score_modes_once(modes, scenario_weights) for scenario, scenario_weights in WEIGHT_SCENARIOS.items()}
    return {
        "weights": {name: dict(value) for name, value in WEIGHT_SCENARIOS.items()},
        "modes": results["baseline"]["modes"],
        "scenarios": {scenario: results[scenario]["modes"] for scenario in results},
        "rankings": {scenario: results[scenario]["rankings"]["custom"] for scenario in results},
        "scoreRankings": {scenario: results[scenario]["scoreRankings"]["custom"] for scenario in results},
        "recommendation": {scenario: results[scenario]["recommendation"]["custom"] for scenario in results},
        "measured": results["baseline"]["measured"],
    }


def evaluate_manifest(manifest: str | Path | Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    manifest_dir: Path | None = None
    if isinstance(manifest, (str, Path)):
        manifest_path = Path(manifest)
        manifest_dir = manifest_path.resolve().parent
        with manifest_path.open(encoding="utf-8") as stream:
            manifest = json.load(stream)
    if isinstance(manifest, Mapping):
        modes = manifest.get("modes")
    else:
        modes = manifest
    if not isinstance(modes, list):
        raise ValueError("manifest must contain a modes list")
    records: list[dict[str, Any]] = []
    for mode in modes:
        if not isinstance(mode, Mapping):
            raise ValueError("each manifest mode must be an object")
        record = dict(mode)
        transcript = record.get("transcript", record.get("transcriptPath"))
        if transcript is not None:
            transcript_path = Path(transcript)
            if manifest_dir is not None and not transcript_path.is_absolute():
                candidate = manifest_dir / transcript_path
                if candidate.exists():
                    transcript = candidate
            summary = summarize_transcript(transcript, participant=str(record.get("name", record.get("mode", ""))))
            record["usage"] = summary["usage"]
            record["activeElapsedSeconds"] = summary["stageTiming"]["activeElapsedSeconds"]
            record["stageCount"] = len(summary["stages"])
            record["transcriptSummary"] = summary
        records.append(record)
    results = {scenario: score_modes(records, weights=scenario_weights) for scenario, scenario_weights in WEIGHT_SCENARIOS.items()}
    return {
        "weights": {name: dict(value) for name, value in WEIGHT_SCENARIOS.items()},
        "modes": results["baseline"]["modes"],
        "scenarios": {scenario: results[scenario]["modes"] for scenario in results},
        "rankings": {scenario: results[scenario]["rankings"]["custom"] for scenario in results},
        "scoreRankings": {scenario: results[scenario]["scoreRankings"]["custom"] for scenario in results},
        "recommendation": {scenario: results[scenario]["recommendation"]["custom"] for scenario in results},
        "measured": results["baseline"]["measured"],
    }


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    summarize = subparsers.add_parser("summarize", help="summarize one participant transcript")
    summarize.add_argument("transcript", type=Path)
    summarize.add_argument("--participant")
    summarize.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)
    evaluate = subparsers.add_parser("evaluate", help="evaluate a mode manifest")
    evaluate.add_argument("manifest", type=Path)
    evaluate.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--pretty", action="store_true", help="indent JSON output")
    args = parser.parse_args()
    value = summarize_transcript(args.transcript, participant=args.participant) if args.command == "summarize" else evaluate_manifest(args.manifest)
    print(json.dumps(value, indent=2 if args.pretty else None, sort_keys=True))


if __name__ == "__main__":
    _main()
