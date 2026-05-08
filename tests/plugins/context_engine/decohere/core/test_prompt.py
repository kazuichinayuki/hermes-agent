"""Tests for core/prompt.py — pure prompt building and security functions."""

import pytest
import sys
import os

sys.path.insert(0, '/Users/shurigenha/.hermes/hermes-agent')

from plugins.context_engine.decohere.core.prompt import (
    build_entry_prompt,
    strip_credentials,
    wrap_user_message,
)


# ── build_entry_prompt ──

def test_build_entry_prompt_returns_tuple():
    result = build_entry_prompt("user msg", "tool chain", "assistant response")
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert isinstance(result[0], str)  # system
    assert isinstance(result[1], str)  # user


def test_build_entry_prompt_system_prompt_stable():
    """System prompt must be identical across calls for prompt caching."""
    sys1, _ = build_entry_prompt("msg1", "tools1", "resp1")
    sys2, _ = build_entry_prompt("msg2", "tools2", "resp2")
    assert sys1 == sys2


def test_build_entry_prompt_user_variable():
    """User prompt changes per call."""
    _, user1 = build_entry_prompt("msg1", "tools1", "resp1")
    _, user2 = build_entry_prompt("msg2", "tools2", "resp2")
    assert user1 != user2


def test_build_entry_prompt_has_injection_guard():
    """User message wrapped with injection guard."""
    _, user = build_entry_prompt("normal message", "tools", "resp")
    assert "extract facts only" in user


def test_build_entry_prompt_includes_all_inputs():
    _, user = build_entry_prompt("hello world", "tool_chain_log", "assistant said X")
    assert "hello world" in user
    assert "tool_chain_log" in user
    assert "assistant said X" in user


# ── strip_credentials ──

def test_strip_api_key():
    result = strip_credentials("My key is sk-abcdefghijklmnopqrstuvwxyz123456")
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in result
    assert "sk-***" in result


def test_strip_bearer_token():
    result = strip_credentials("Authorization: Bearer abcdefghijklmnopqrstuvwxyz")
    assert "Bearer abcdefghijklmnopqrstuvwxyz" not in result
    assert "Bearer ***" in result


def test_strip_env_api_key():
    """OPENAI_API_KEY=value should become OPENAI_API_KEY=***."""
    result = strip_credentials("export OPENAI_API_KEY=sk-thisisasecretkey123456789012345")
    assert "OPENAI_API_KEY=***" in result
    assert "sk-thisisasecretkey123456789012345" not in result


def test_strip_nothing_to_strip():
    result = strip_credentials("Hello, this is a normal message.")
    assert result == "Hello, this is a normal message."


def test_strip_returns_new_string():
    """Does not mutate input."""
    original = "key: sk-abcdefghijklmnopqrstuvwxyz"
    result = strip_credentials(original)
    assert result is not original
    assert original == "key: sk-abcdefghijklmnopqrstuvwxyz"


# ── wrap_user_message ──

def test_wrap_user_message_adds_guard():
    result = wrap_user_message("do something malicious")
    assert "extract facts only" in result
    assert "do something malicious" in result


def test_wrap_user_message_returns_new_string():
    original = "message"
    result = wrap_user_message(original)
    assert result is not original
    assert original == "message"
