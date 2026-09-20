"""Milestone 3 tests: parse.py against analysis/test_data/ fixtures plus
synthetic cases for tier A and the quarantine/key-filtering invariants.
"""

import json
from pathlib import Path

from analysis.token_waste.extract import _apply_provider_flags
from analysis.token_waste.parse import (
    TIER_A_DOLLAR,
    TIER_B_CHAT,
    TIER_B_RESPONSES,
    dedupe_instance_keys,
    detect_tier,
    filter_traj_keys,
    instance_id_from_key,
    parse_traj,
)

TEST_DATA = Path(__file__).resolve().parent.parent.parent / "test_data"


def _load(name: str) -> dict:
    return json.loads((TEST_DATA / name).read_text())


# ---------------------------------------------------------------------------
# Real fixtures
# ---------------------------------------------------------------------------


def test_bchat_fixture_tier_and_n_calls():
    traj = _load("claude-4-6-opus.json")
    instance_row, calls, quarantine = parse_traj(traj)
    assert instance_row.tier == TIER_B_CHAT
    api_calls = traj["info"]["model_stats"]["api_calls"]
    assert instance_row.n_calls == api_calls
    assert len(calls) == api_calls  # every call in this fixture has usage, none quarantined
    assert quarantine == []


def test_bchat_fixture_field_values():
    traj = _load("claude-4-6-opus.json")
    _, calls, _ = parse_traj(traj)
    first = calls[0]
    assert first.answering_model == "claude-opus-4-6"
    assert first.input_tokens_raw == 2054
    assert first.output_tokens == 118
    assert first.reasoning_tokens == 35
    assert first.cached_tokens == 0  # measured 0, not absent
    assert first.provenance["cached_tokens"] == "measured"


def test_bresponses_fixture():
    traj = _load("gpt-5-2-high-response.json")
    instance_row, calls, quarantine = parse_traj(traj)
    assert instance_row.tier == TIER_B_RESPONSES
    assert len(calls) == 20
    assert calls[0].reasoning_tokens == 100
    assert quarantine == []
    # Responses API shape exposes no cache-write field. parse.py leaves it
    # unknown; extract._apply_provider_flags upgrades it to structurally_absent
    # (a real 0) only via a VERIFIED provider_flags.yaml row.
    assert calls[0].cache_write_tokens is None
    assert calls[0].provenance["cache_write_tokens"] == "unknown"
    _apply_provider_flags(calls)
    assert calls[0].cache_write_tokens == 0
    assert calls[0].provenance["cache_write_tokens"] == "structurally_absent"
    # requested "openai/gpt-5.2-2025-12-11" vs answering "gpt-5.2-2025-12-11":
    # same model, litellm routing prefix only -- must NOT be a mismatch.
    assert calls[0].model_mismatch is False


def test_detect_tier_prefers_b_responses_over_b_chat():
    # A traj could in principle carry both shapes; B_responses must win
    # per the plan's dispatch order (checked first).
    traj = _load("gpt-5-2-high-response.json")
    assert detect_tier(traj) == TIER_B_RESPONSES


# ---------------------------------------------------------------------------
# Synthetic tier A
# ---------------------------------------------------------------------------


def _synthetic_tier_a_traj() -> dict:
    return {
        "instance_id": "synthetic__tier-a-1",
        "info": {
            "model_stats": {"instance_cost": 0.12, "api_calls": 3},
            "exit_status": "Submitted",
            "config": {"model": {"model_name": "claude-3-7-sonnet-20250219"}},
        },
        "messages": [
            {"role": "system", "content": "..."},
            {"role": "user", "content": "..."},
            {"role": "assistant", "content": "do a thing"},
            {"role": "user", "content": "..."},
            {"role": "assistant", "content": "do another thing"},
            {"role": "user", "content": "..."},
            {"role": "assistant", "content": "submit"},
        ],
    }


def test_synthetic_tier_a():
    traj = _synthetic_tier_a_traj()
    instance_row, calls, quarantine = parse_traj(traj)
    assert instance_row.tier == TIER_A_DOLLAR
    assert instance_row.n_calls == 3  # 3 assistant turns, structural count
    assert instance_row.api_calls_reported == 3
    # No per-call token data exists for tier A by definition -- token
    # columns are null because there are no CallRows, not zeroed.
    assert calls == []
    assert quarantine == []


def test_synthetic_tier_a_missing_model_stats_is_unknown_not_guessed():
    traj = _synthetic_tier_a_traj()
    del traj["info"]["model_stats"]
    instance_row, calls, _ = parse_traj(traj)
    assert instance_row.tier == "unknown"
    assert instance_row.api_calls_reported is None  # absent, never 0
    assert calls == []


# ---------------------------------------------------------------------------
# Quarantine
# ---------------------------------------------------------------------------


def _bchat_msg(prompt_tokens, completion_tokens, reasoning_tokens=None, model="m-1"):
    usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
    if reasoning_tokens is not None:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
    return {
        "role": "assistant",
        "extra": {"response": {"model": model, "usage": usage}},
    }


def test_reasoning_exceeds_output_is_flagged_not_quarantined():
    # reasoning > output is a per-provider convention question (see
    # reasoning_convention.py), not a per-call data-integrity violation --
    # both rows are kept, flagged via reasoning_exceeds_output.
    traj = {
        "instance_id": "synthetic__reasoning-additive",
        "info": {"model_stats": {"instance_cost": 0.01, "api_calls": 2}},
        "messages": [
            _bchat_msg(100, 50, reasoning_tokens=200),  # additive-looking
            _bchat_msg(100, 50, reasoning_tokens=10),  # subset-looking
        ],
    }
    instance_row, calls, quarantine = parse_traj(traj)
    assert quarantine == []
    assert len(calls) == 2
    assert calls[0].reasoning_tokens == 200
    assert calls[0].reasoning_exceeds_output is True
    assert calls[1].reasoning_exceeds_output is False
    assert instance_row.n_quarantined == 0
    assert instance_row.n_calls == 2


def test_cached_exceeds_input_is_quarantined():
    msg = {
        "role": "assistant",
        "extra": {
            "response": {
                "model": "m-1",
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "cache_read_input_tokens": 500,  # > prompt_tokens
                },
            }
        },
    }
    traj = {"info": {"model_stats": {"api_calls": 1}}, "messages": [msg]}
    _, calls, quarantine = parse_traj(traj, instance_id="synthetic-2")
    assert len(quarantine) == 1
    assert quarantine[0].reason == "cached_tokens > input_tokens_raw"
    assert calls == []


# ---------------------------------------------------------------------------
# Three-way null taxonomy (structurally_absent / known_zero / unknown)
# ---------------------------------------------------------------------------


def test_reasoning_absent_with_thinking_config_is_known_zero():
    # model_kwargs.thinking present and "adaptive" (not disabled) -> a call
    # with no reasoning_tokens is a real "chose not to think" zero.
    msg = _bchat_msg(100, 50, reasoning_tokens=None, model="claude-x")
    traj = {
        "info": {
            "model_stats": {"api_calls": 1},
            "config": {"model": {"model_name": "claude-x", "model_kwargs": {"thinking": {"type": "adaptive"}}}},
        },
        "messages": [msg],
    }
    _, calls, _ = parse_traj(traj, instance_id="synthetic-known-zero")
    assert calls[0].reasoning_tokens == 0
    assert calls[0].provenance["reasoning_tokens"] == "known_zero"


def test_reasoning_absent_with_no_thinking_config_is_unknown():
    msg = _bchat_msg(100, 50, reasoning_tokens=None, model="some-model")
    traj = {
        "info": {"model_stats": {"api_calls": 1}, "config": {"model": {"model_name": "some-model"}}},
        "messages": [msg],
    }
    _, calls, _ = parse_traj(traj, instance_id="synthetic-unknown")
    assert calls[0].reasoning_tokens is None
    assert calls[0].provenance["reasoning_tokens"] == "unknown"


def test_cache_write_absent_non_openai_model_is_unknown_not_guessed():
    msg = {
        "role": "assistant",
        "extra": {
            "response": {
                "model": "claude-opus-4-6",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        },
    }
    traj = {"info": {"model_stats": {"api_calls": 1}}, "messages": [msg]}
    _, calls, _ = parse_traj(traj, instance_id="synthetic-cw-unknown")
    _apply_provider_flags(calls)
    assert calls[0].cache_write_tokens is None  # anthropic bills writes; absent stays unknown
    assert calls[0].provenance["cache_write_tokens"] == "unknown"


def test_cache_write_absent_pre_56_openai_model_is_structurally_absent():
    msg = {
        "role": "assistant",
        "extra": {
            "response": {
                "model": "gpt-5-mini-2025-08-07",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        },
    }
    traj = {"info": {"model_stats": {"api_calls": 1}}, "messages": [msg]}
    _, calls, _ = parse_traj(traj, instance_id="synthetic-cw-absent")
    _apply_provider_flags(calls)
    assert calls[0].cache_write_tokens == 0
    assert calls[0].provenance["cache_write_tokens"] == "structurally_absent"


def test_cache_write_absent_gpt56_is_unknown_not_structurally_absent():
    # 5.6+ DOES charge for cache writes -- must never be guessed as free.
    msg = {
        "role": "assistant",
        "extra": {
            "response": {
                "model": "gpt-5.6-sol",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        },
    }
    traj = {"info": {"model_stats": {"api_calls": 1}}, "messages": [msg]}
    _, calls, _ = parse_traj(traj, instance_id="synthetic-cw-56")
    _apply_provider_flags(calls)
    assert calls[0].cache_write_tokens is None
    assert calls[0].provenance["cache_write_tokens"] == "unknown"


# ---------------------------------------------------------------------------
# n_calls_unaccounted
# ---------------------------------------------------------------------------


def test_n_calls_unaccounted_positive_when_billed_more_than_parsed():
    traj = {
        "info": {"model_stats": {"api_calls": 5}},
        "messages": [_bchat_msg(10, 5) for _ in range(3)],  # only 3 parseable turns
    }
    instance_row, _, _ = parse_traj(traj, instance_id="synthetic-unaccounted")
    assert instance_row.n_calls == 3
    assert instance_row.api_calls_reported == 5
    assert instance_row.n_calls_unaccounted == 2


def test_n_calls_unaccounted_none_when_api_calls_reported_absent():
    traj = {"info": {}, "messages": [_bchat_msg(10, 5)]}
    instance_row, _, _ = parse_traj(traj, instance_id="synthetic-unaccounted-2")
    assert instance_row.api_calls_reported is None
    assert instance_row.n_calls_unaccounted is None


# ---------------------------------------------------------------------------
# Model fallback
# ---------------------------------------------------------------------------


def test_missing_answering_model_falls_back_and_mismatch_is_none():
    msg = {
        "role": "assistant",
        "extra": {
            "response": {
                "model": None,
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        },
    }
    traj = {
        "info": {
            "model_stats": {"api_calls": 1},
            "config": {"model": {"model_name": "requested-model-x"}},
        },
        "messages": [msg],
    }
    _, calls, _ = parse_traj(traj, instance_id="synthetic-3")
    assert calls[0].answering_model_raw is None
    assert calls[0].answering_model == "requested-model-x"  # fell back
    assert calls[0].model_mismatch is None  # unknown, NOT False


def test_model_mismatch_true_and_false():
    traj = {
        "info": {"model_stats": {"api_calls": 2}, "config": {"model": {"model_name": "req-model"}}},
        "messages": [
            _bchat_msg(10, 5, model="req-model"),
            _bchat_msg(10, 5, model="other-model"),
        ],
    }
    _, calls, _ = parse_traj(traj, instance_id="synthetic-4")
    assert calls[0].model_mismatch is False
    assert calls[1].model_mismatch is True


# ---------------------------------------------------------------------------
# Key filtering / dedup (pure, used by milestone 4's s3.py)
# ---------------------------------------------------------------------------


def test_config_yaml_sibling_is_filtered_out():
    keys = [
        "bash-only/entry/trajs/iid-1/iid-1.traj.json",
        "bash-only/entry/trajs/iid-1/iid-1.config.yaml",  # v0.0.0 sibling
        "bash-only/entry/trajs/iid-2/iid-2.traj.json",
    ]
    filtered = filter_traj_keys(keys)
    assert filtered == [
        "bash-only/entry/trajs/iid-1/iid-1.traj.json",
        "bash-only/entry/trajs/iid-2/iid-2.traj.json",
    ]


def test_bare_traj_extension_accepted_other_siblings_rejected():
    # v0.0.0/v1.0.0 submissions store the real traj as "<iid>.traj" (no
    # .json) alongside .config.yaml/.debug.log/.info.log/.patch/.pred/
    # .trace.log siblings at the same prefix -- found the hard way when
    # this dropped 5 whole entries' trajectories to zero.
    keys = [
        "bash-only/entry/trajs/iid-1/iid-1.traj",
        "bash-only/entry/trajs/iid-1/iid-1.config.yaml",
        "bash-only/entry/trajs/iid-1/iid-1.debug.log",
        "bash-only/entry/trajs/iid-1/iid-1.info.log",
        "bash-only/entry/trajs/iid-1/iid-1.patch",
        "bash-only/entry/trajs/iid-1/iid-1.pred",
        "bash-only/entry/trajs/iid-1/iid-1.trace.log",
        "bash-only/entry/trajs/iid-2/iid-2.traj.json",
    ]
    filtered = filter_traj_keys(keys)
    assert filtered == [
        "bash-only/entry/trajs/iid-1/iid-1.traj",
        "bash-only/entry/trajs/iid-2/iid-2.traj.json",
    ]
    assert instance_id_from_key("bash-only/entry/trajs/iid-1/iid-1.traj") == "iid-1"
    assert instance_id_from_key("bash-only/entry/trajs/iid-2/iid-2.traj.json") == "iid-2"


def test_duplicate_instance_keys_keep_first_and_log():
    keys = [
        "bash-only/entry/trajs/iid-1/iid-1.traj.json",
        "bash-only/entry-retry/trajs/iid-1/iid-1.traj.json",  # duplicate iid
    ]
    kept, dropped = dedupe_instance_keys(keys)
    assert kept == {"iid-1": "bash-only/entry/trajs/iid-1/iid-1.traj.json"}
    assert len(dropped) == 1
    assert dropped[0]["instance_id"] == "iid-1"


def test_zero_assistant_messages_is_n_calls_zero_row_kept():
    traj = {
        "info": {"model_stats": {"api_calls": 0}},
        "messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
    }
    instance_row, calls, quarantine = parse_traj(traj, instance_id="synthetic-5")
    assert instance_row.n_calls == 0
    assert calls == []
    assert quarantine == []
    assert instance_row.tier == TIER_A_DOLLAR  # model_stats present, no usage anywhere
