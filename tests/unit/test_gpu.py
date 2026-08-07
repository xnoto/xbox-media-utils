"""Tests for cooperative ROCm activity gating."""

import json
import subprocess

import pytest

from xbox_media_utils.core.gpu import (
    GpuStatusError,
    GpuUsage,
    get_rocm_gpu_usage,
    wait_for_rocm_gpu_idle,
)


def test_get_rocm_gpu_usage_returns_busiest_card(monkeypatch):
    payload = {
        "card0": {"GPU use (%)": "2", "GPU memory use (%)": "4"},
        "card1": {"GPU use (%)": "75", "GPU memory use (%)": "60"},
    }
    monkeypatch.setattr(
        "xbox_media_utils.core.gpu.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout=json.dumps(payload), stderr=""
        ),
    )

    assert get_rocm_gpu_usage() == GpuUsage(use_percent=75, memory_percent=60)


def test_get_rocm_gpu_usage_fails_closed_on_invalid_output(monkeypatch):
    monkeypatch.setattr(
        "xbox_media_utils.core.gpu.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="not json", stderr=""
        ),
    )

    with pytest.raises(GpuStatusError):
        get_rocm_gpu_usage()


def test_wait_for_rocm_gpu_idle_retries_until_compute_and_memory_are_idle():
    readings = iter(
        [
            GpuUsage(use_percent=90, memory_percent=80),
            GpuUsage(use_percent=0, memory_percent=50),
            GpuUsage(use_percent=2, memory_percent=4),
        ]
    )
    messages = []
    sleeps = []

    result = wait_for_rocm_gpu_idle(
        max_use_percent=5,
        max_memory_percent=10,
        poll_seconds=30,
        logger=messages.append,
        usage_reader=lambda: next(readings),
        sleeper=sleeps.append,
    )

    assert result == GpuUsage(use_percent=2, memory_percent=4)
    assert len(messages) == 2
    assert sleeps == [30, 30]
