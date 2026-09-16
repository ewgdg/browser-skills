import importlib.util
import json
from pathlib import Path

import pytest


def load_analyzer():
    path = Path(__file__).with_name("analyze.py")
    spec = importlib.util.spec_from_file_location("benchmark_analyze", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_transcript(tmp_path, records):
    path = tmp_path / "participant.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return path


def usage(total, *, input=0, output=0, cache_read=0, cache_write=0, reasoning=0):
    return {
        "input": input,
        "output": output,
        "cacheRead": cache_read,
        "cacheWrite": cache_write,
        "totalTokens": total,
        "cost": {"total": 0.01},
        "reasoning": reasoning,
    }


def stage_start(timestamp="2026-09-16T22:00:00Z", request_id="req-1"):
    return {
        "type": "custom_message",
        "customType": "agent-coordination.message-delivery",
        "timestamp": timestamp,
        "content": json.dumps(
            {"messages": [{"kind": "request", "requestMessageId": request_id, "title": "task"}]}
        ),
    }


def assistant(timestamp, token_usage, content=None):
    return {
        "type": "message",
        "timestamp": timestamp,
        "message": {"role": "assistant", "usage": token_usage, "content": content or []},
    }


def answer(timestamp, token_usage):
    return assistant(
        timestamp,
        token_usage,
        [{"type": "toolCall", "name": "agent_message", "arguments": {"operation": "answer"}}],
    )


def test_summarize_detects_stages_and_sanitizes_transcript(tmp_path):
    analyze = load_analyzer()
    transcript = write_transcript(
        tmp_path,
        [
            stage_start(),
            assistant(
                "2026-09-16T22:00:01Z",
                usage(100, input=50, output=10, cache_read=30, cache_write=10, reasoning=7),
                [{"type": "text", "text": "do not retain this"}],
            ),
            {"type": "toolResult", "timestamp": "2026-09-16T22:00:02Z", "content": [{"type": "text", "text": "result"}]},
            answer("2026-09-16T22:00:03Z", usage(50, input=20, output=5, cache_read=15, reasoning=4)),
        ],
    )

    summary = analyze.summarize_transcript(transcript)

    assert len(summary["stages"]) == 1
    stage = summary["stages"][0]
    assert stage["requestMessageId"] == "req-1"
    assert stage["activeElapsedSeconds"] == pytest.approx(3)
    assert stage["usage"]["totalTokens"] == 150
    assert stage["usage"]["cacheRead"] == 45
    assert stage["usage"]["reasoning"] == 11
    assert summary["usage"]["totalTokens"] == 150
    assert summary["usage"]["reasoning"] == 11
    assert summary["diagnostics"]["toolResultTextChars"] == len("result")
    assert summary["messages"][0]["timestamp"] == "2026-09-16T22:00:01Z"
    assert "do not retain this" not in json.dumps(summary)


def test_usage_total_is_not_recomputed_with_reasoning(tmp_path):
    analyze = load_analyzer()
    transcript = write_transcript(
        tmp_path,
        [stage_start(), assistant("2026-09-16T22:00:01Z", usage(12, input=4, output=3, reasoning=99)), answer("2026-09-16T22:00:02Z", usage(8, reasoning=20))],
    )
    summary = analyze.summarize_transcript(transcript)
    assert summary["usage"]["totalTokens"] == 20
    assert summary["usage"]["reasoning"] == 119


def test_summarize_counts_nested_pi_tool_result_text(tmp_path):
    analyze = load_analyzer()
    exposed = "visible tool output\nwith two lines"
    transcript = write_transcript(
        tmp_path,
        [
            stage_start(),
            assistant("2026-09-16T22:00:01Z", usage(10)),
            {
                "type": "message",
                "timestamp": "2026-09-16T22:00:02Z",
                "message": {
                    "role": "toolResult",
                    "content": [{"type": "text", "text": "prefix"}, {"type": "text", "text": exposed}],
                },
            },
            answer("2026-09-16T22:00:03Z", usage(10)),
        ],
    )

    summary = analyze.summarize_transcript(transcript)

    assert summary["diagnostics"]["toolResultTextChars"] == len("prefix") + len(exposed)
    assert exposed not in json.dumps(summary)


def test_missing_or_zero_usage_fails_closed(tmp_path):
    analyze = load_analyzer()
    missing = write_transcript(tmp_path, [stage_start(), assistant("2026-09-16T22:00:01Z", None)])
    zero = write_transcript(tmp_path, [stage_start(), assistant("2026-09-16T22:00:01Z", usage(0))])
    with pytest.raises(ValueError, match="usage"):
        analyze.summarize_transcript(missing)
    with pytest.raises(ValueError, match="usage"):
        analyze.summarize_transcript(zero)


def test_decision_matrix_scores_ratios_and_vetoes_failed_recovery():
    analyze = load_analyzer()
    modes = [
        {
            "name": "cli",
            "usage": {"totalTokens": 100},
            "activeElapsedSeconds": 10,
            "correctness": {"passed": 2, "total": 2},
            "recovery": {"passed": 1, "total": 1},
            "simplicity": 5,
        },
        {
            "name": "persistent",
            "usage": {"totalTokens": 200},
            "activeElapsedSeconds": 5,
            "correctness": {"passed": 2, "total": 2},
            "recovery": {"passed": 0, "total": 1},
            "simplicity": 2,
        },
    ]

    result = analyze.score_modes(modes)
    cli, persistent = result["modes"]
    assert cli["scores"]["tokens"] == pytest.approx(5)
    assert cli["scores"]["latency"] == pytest.approx(2.5)
    assert cli["overall"] == pytest.approx(95)
    assert cli["eligible"] is True
    assert persistent["scores"]["tokens"] == pytest.approx(2.5)
    assert persistent["scores"]["latency"] == pytest.approx(5)
    assert persistent["eligible"] is False
    assert result["rankings"]["baseline"][0] == "cli"
    assert result["recommendation"]["baseline"] == "cli"


def test_efficiency_requires_complete_workload_and_reports_empirical_subtotal():
    analyze = load_analyzer()
    result = analyze.score_modes(
        [
            {
                "name": "complete",
                "usage": {"totalTokens": 100},
                "activeElapsedSeconds": 10,
                "correctness": 1,
                "recovery": 1,
                "simplicity": 5,
                "expectedStageCount": 4,
                "stageCount": 4,
            },
            {
                "name": "incomplete",
                "usage": {"totalTokens": 1},
                "activeElapsedSeconds": 1,
                "correctness": 1,
                "recovery": 1,
                "simplicity": 5,
                "expectedStageCount": 4,
                "stageCount": 3,
            },
        ]
    )
    complete, incomplete = result["modes"]
    assert incomplete["empiricalSubtotal"] == pytest.approx(55)
    assert incomplete["scores"]["tokens"] == 0
    assert incomplete["scores"]["latency"] == 0
    assert result["recommendation"]["baseline"] == "complete"
    assert result["rankings"]["baseline"][0] == "complete"
    assert result["scoreRankings"]["baseline"][0] == "complete"
    assert incomplete["eligible"] is False


def test_cheap_incomplete_arm_does_not_change_complete_efficiency_scores_or_order():
    analyze = load_analyzer()
    complete_modes = [
        {"name": "first", "usage": {"totalTokens": 100}, "activeElapsedSeconds": 10, "correctness": 1, "recovery": 1, "simplicity": 5, "expectedStageCount": 4, "stageCount": 4},
        {"name": "second", "usage": {"totalTokens": 200}, "activeElapsedSeconds": 20, "correctness": 1, "recovery": 1, "simplicity": 5, "expectedStageCount": 4, "stageCount": 4},
    ]
    with_incomplete = complete_modes + [
        {"name": "cheap-incomplete", "usage": {"totalTokens": 1}, "activeElapsedSeconds": 1, "correctness": 1, "recovery": 1, "simplicity": 5, "expectedStageCount": 4, "stageCount": 3},
    ]

    without = {mode["name"]: mode for mode in analyze.score_modes(complete_modes)["modes"]}
    with_arm = {mode["name"]: mode for mode in analyze.score_modes(with_incomplete)["modes"]}

    for name in ("first", "second"):
        assert with_arm[name]["scores"]["tokens"] == pytest.approx(without[name]["scores"]["tokens"])
        assert with_arm[name]["scores"]["latency"] == pytest.approx(without[name]["scores"]["latency"])
    assert analyze.score_modes(with_incomplete)["scoreRankings"]["baseline"][:2] == ["first", "second"]


def test_all_incomplete_modes_receive_no_efficiency_score_or_recommendation():
    analyze = load_analyzer()
    result = analyze.score_modes([
        {"name": "a", "usage": {"totalTokens": 1}, "activeElapsedSeconds": 1, "correctness": 1, "recovery": 1, "simplicity": 5, "expectedStageCount": 4, "stageCount": 3},
        {"name": "b", "usage": {"totalTokens": 2}, "activeElapsedSeconds": 2, "correctness": 1, "recovery": 1, "simplicity": 5, "expectedStageCount": 4, "stageCount": 2},
    ])
    assert result["recommendation"]["baseline"] is None
    assert all(mode["scores"]["tokens"] == 0 and mode["scores"]["latency"] == 0 for mode in result["modes"])


def test_alternative_weight_rankings_are_reported():
    analyze = load_analyzer()
    result = analyze.score_modes(
        [
            {"name": "a", "usage": {"totalTokens": 100}, "activeElapsedSeconds": 10, "correctness": 1, "recovery": 1, "simplicity": 5},
            {"name": "b", "usage": {"totalTokens": 200}, "activeElapsedSeconds": 5, "correctness": 1, "recovery": 1, "simplicity": 2},
        ]
    )
    assert set(result["rankings"]) == {"baseline", "token-heavy", "reliability-heavy"}
    assert all(result["rankings"][key] for key in result["rankings"])
