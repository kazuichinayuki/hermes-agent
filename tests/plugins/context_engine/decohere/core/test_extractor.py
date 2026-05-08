"""Tests for core/extractor.py — pure mechanical extraction functions."""

import pytest
import sys
import os

# Add repo root to path
sys.path.insert(0, '/Users/shurigenha/.hermes/hermes-agent')

from plugins.context_engine.decohere.core.extractor import (
    last_turn_messages,
    tool_calls_from_messages,
    files_from_messages,
    mechanical_fields,
    tool_chain_log,
    summarise_args,
    summarise_tool_result,
)
from plugins.context_engine.decohere.types import MechanicalFields


# ── Fixtures ──

def _make_msg(role, content=None, tool_calls=None, tool_name=None):
    """Helper to build consistent message dicts."""
    msg = {"role": role, "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if tool_name:
        msg["tool_name"] = tool_name
    return msg


def _make_tool_call(name, arguments):
    """Helper for tool_call objects."""
    import json
    return {
        "function": {
            "name": name,
            "arguments": json.dumps(arguments) if isinstance(arguments, dict) else arguments
        }
    }


@pytest.fixture
def single_turn_no_tools():
    """A simple single-turn conversation with no tool calls."""
    return [
        _make_msg("user", "What is the weather?"),
        _make_msg("assistant", "The weather is sunny."),
    ]


@pytest.fixture
def single_turn_with_tools():
    """A single turn with read_file and patch tool calls."""
    return [
        _make_msg("user", "Read and modify the config file."),
        _make_msg("assistant", None, tool_calls=[
            _make_tool_call("read_file", {"path": "/Users/test/config.yaml"}),
            _make_tool_call("patch", {"path": "/Users/test/config.yaml", "old_string": "x", "new_string": "y"}),
        ]),
        _make_msg("tool", '{"success": true}', tool_name="read_file"),
        _make_msg("tool", '{"success": true}', tool_name="patch"),
        _make_msg("assistant", "Done editing."),
    ]


@pytest.fixture
def multi_turn():
    """Two turns: first with tools, second text-only."""
    return [
        _make_msg("user", "Search for docs."),
        _make_msg("assistant", None, tool_calls=[
            _make_tool_call("web_search", {"query": "python asyncio"}),
        ]),
        _make_msg("tool", '{"data": "results here"}', tool_name="web_search"),
        _make_msg("assistant", "Found documentation."),
        _make_msg("user", "Thanks, that helped."),
        _make_msg("assistant", "You're welcome!"),
    ]


@pytest.fixture
def empty_messages():
    return []


# ── last_turn_messages ──

def test_last_turn_single(multi_turn):
    """Last turn should be the final user+assistant exchange."""
    result = last_turn_messages(multi_turn)
    assert len(result) == 2
    assert result[0]["role"] == "user"
    assert result[0]["content"] == "Thanks, that helped."
    assert result[1]["content"] == "You're welcome!"


def test_last_turn_no_user_boundary():
    """If no user message boundary, return all messages."""
    msgs = [_make_msg("assistant", "Just an assistant talking.")]
    result = last_turn_messages(msgs)
    assert len(result) == 1


def test_last_turn_empty(empty_messages):
    """Empty input returns empty list."""
    result = last_turn_messages(empty_messages)
    assert result == []


def test_last_turn_does_not_mutate(multi_turn):
    """Original list must remain unchanged."""
    original_len = len(multi_turn)
    _ = last_turn_messages(multi_turn)
    assert len(multi_turn) == original_len


# ── tool_calls_from_messages ──

def test_tool_calls_single_turn(single_turn_with_tools):
    result = tool_calls_from_messages(single_turn_with_tools)
    assert len(result) == 2
    assert result[0]["name"] == "read_file"
    assert "path" in result[0]["args_summary"]
    assert result[1]["name"] == "patch"


def test_tool_calls_no_tools(single_turn_no_tools):
    result = tool_calls_from_messages(single_turn_no_tools)
    assert result == ()


def test_tool_calls_empty(empty_messages):
    result = tool_calls_from_messages(empty_messages)
    assert result == ()


# ── files_from_messages ──

def test_files_extraction(single_turn_with_tools):
    result = files_from_messages(single_turn_with_tools)
    # Should extract config.yaml path, sanitized home dir
    assert len(result) >= 1
    assert any("config.yaml" in f for f in result)


def test_files_no_path_tools(single_turn_no_tools):
    result = files_from_messages(single_turn_no_tools)
    assert result == ()


def test_files_empty(empty_messages):
    result = files_from_messages(empty_messages)
    assert result == ()


def test_files_deduplicates():
    """Same file path should only appear once."""
    msgs = [
        _make_msg("user", "Read it twice."),
        _make_msg("assistant", None, tool_calls=[
            _make_tool_call("read_file", {"path": "/tmp/x.txt"}),
            _make_tool_call("read_file", {"path": "/tmp/x.txt"}),
        ]),
    ]
    result = files_from_messages(msgs)
    assert len(result) == 1


# ── mechanical_fields ──

def test_mechanical_fields(single_turn_with_tools):
    result = mechanical_fields(single_turn_with_tools)
    assert isinstance(result, MechanicalFields)
    assert len(result.tools) == 2
    assert len(result.files_touched) >= 1


def test_mechanical_fields_empty(empty_messages):
    result = mechanical_fields(empty_messages)
    assert isinstance(result, MechanicalFields)
    assert result.tools == ()
    assert result.files_touched == ()


# ── tool_chain_log ──

def test_tool_chain_log_single_turn(single_turn_with_tools):
    result = tool_chain_log(single_turn_with_tools)
    assert "read_file" in result
    assert "patch" in result
    assert "Assistant response:" in result
    assert "Done editing." in result


def test_tool_chain_log_no_tools(single_turn_no_tools):
    result = tool_chain_log(single_turn_no_tools)
    assert "Assistant response:" in result
    assert "sunny" in result


def test_tool_chain_log_empty(empty_messages):
    result = tool_chain_log(empty_messages)
    assert result == ""


# ── summarise_args ──

def test_summarise_args_known():
    result = summarise_args("read_file", '{"path": "/etc/hosts", "offset": 10}')
    assert "path" in result
    assert "/etc/hosts" in result


def test_summarise_args_unknown_tool():
    result = summarise_args("unknown_tool", '{"x": 1}')
    assert result == ""


def test_summarise_args_bad_json():
    result = summarise_args("read_file", "not json")
    assert result == ""


def test_summarise_args_dict_input():
    """args_raw can be a dict, not just json string."""
    result = summarise_args("read_file", {"path": "/x"})
    assert "/x" in result


def test_summarise_args_no_truncation():
    """Must NEVER truncate — even for very long strings."""
    long_path = "/a" * 200
    result = summarise_args("read_file", '{"path": "' + long_path + '"}')
    assert long_path in result
    assert "..." not in result


# ── summarise_tool_result ──

def test_summarise_json_dict():
    result = summarise_tool_result('{"key1": 1, "key2": 2, "key3": 3}')
    assert "key1" in result
    assert "chars" in result


def test_summarise_json_array():
    result = summarise_tool_result('[1, 2, 3, 4, 5]')
    assert "array[5]" in result


def test_summarise_text():
    result = summarise_tool_result("just plain text")
    assert "chars" in result


def test_summarise_no_truncation():
    """Must NEVER include truncated snippets of content."""
    long_text = "X" * 5000
    result = summarise_tool_result(long_text)
    assert "X" * 5000 not in result  # full text not included
    assert "5000 chars" in result  # only size description
    assert "..." not in result  # no truncation marker
