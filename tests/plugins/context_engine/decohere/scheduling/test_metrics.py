"""Tests for scheduling/metrics.py — in-memory metrics collection."""

import pytest
import sys
import os

sys.path.insert(0, '/Users/shurigenha/.hermes/hermes-agent')

from plugins.context_engine.decohere.scheduling.metrics import MetricsCollector


# ── MetricsCollector ──

def test_metrics_initial_state():
    mc = MetricsCollector()
    assert mc.attempted == 0
    assert mc.succeeded == 0
    assert mc.failed == 0
    assert mc.timed_out == 0
    assert mc.degraded == 0


def test_metrics_record_attempt():
    mc = MetricsCollector()
    mc.record_attempt()
    assert mc.attempted == 1


def test_metrics_record_success():
    mc = MetricsCollector()
    mc.record_attempt()
    mc.record_success(123.4)
    assert mc.succeeded == 1
    assert mc.total_elapsed_ms == 123.4


def test_metrics_record_failure():
    mc = MetricsCollector()
    mc.record_attempt()
    mc.record_failure(50.0)
    assert mc.failed == 1


def test_metrics_record_timeout():
    mc = MetricsCollector()
    mc.record_attempt()
    mc.record_timeout(5000.0)
    assert mc.timed_out == 1


def test_metrics_record_degraded():
    mc = MetricsCollector()
    mc.record_degraded()
    assert mc.degraded == 1


def test_metrics_failure_rate():
    mc = MetricsCollector()
    for _ in range(3):
        mc.record_attempt()
    mc.record_success(1.0)
    mc.record_failure(1.0)
    mc.record_timeout(1.0)
    assert mc.failure_rate() == "2/3"


def test_metrics_failure_rate_zero_attempts():
    mc = MetricsCollector()
    assert mc.failure_rate() == "0/0"


def test_metrics_success_rate():
    mc = MetricsCollector()
    for _ in range(4):
        mc.record_attempt()
    mc.record_success(1.0)
    mc.record_success(1.0)
    mc.record_success(1.0)
    mc.record_failure(1.0)
    assert mc.success_rate() == 0.75


def test_metrics_success_rate_no_attempts():
    mc = MetricsCollector()
    assert mc.success_rate() == 1.0


def test_metrics_snapshot():
    mc = MetricsCollector()
    mc.record_attempt()
    mc.record_success(100.0)
    snap = mc.snapshot()
    assert snap["attempted"] == 1
    assert snap["succeeded"] == 1
    assert snap["failed"] == 0
    assert snap["avg_latency_ms"] == 100.0


def test_metrics_deterministic():
    """Multiple calls with same inputs produce same outputs."""
    mc1 = MetricsCollector()
    mc1.record_attempt()
    mc1.record_success(50.0)
    snap1 = mc1.snapshot()

    mc2 = MetricsCollector()
    mc2.record_attempt()
    mc2.record_success(50.0)
    snap2 = mc2.snapshot()

    assert snap1 == snap2
