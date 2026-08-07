"""ROCm GPU activity checks for cooperative background recoding."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from typing import Callable


class GpuStatusError(RuntimeError):
    """Raised when GPU activity cannot be determined safely."""


@dataclass(frozen=True)
class GpuUsage:
    """Current accelerator and VRAM utilization percentages."""

    use_percent: int
    memory_percent: int


def get_rocm_gpu_usage(command: str = "rocm-smi") -> GpuUsage:
    """Read the busiest AMD GPU from ``rocm-smi`` JSON output."""
    result = subprocess.run(
        [command, "--showuse", "--showmemuse", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise GpuStatusError(result.stderr.strip() or "rocm-smi failed")
    try:
        cards = json.loads(result.stdout)
        usages = [
            GpuUsage(
                use_percent=int(values["GPU use (%)"]),
                memory_percent=int(values["GPU memory use (%)"]),
            )
            for values in cards.values()
        ]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        raise GpuStatusError(f"Could not parse rocm-smi output: {e}") from e
    if not usages:
        raise GpuStatusError("rocm-smi returned no GPUs")
    return GpuUsage(
        use_percent=max(usage.use_percent for usage in usages),
        memory_percent=max(usage.memory_percent for usage in usages),
    )


def wait_for_rocm_gpu_idle(
    max_use_percent: int,
    max_memory_percent: int,
    poll_seconds: int,
    logger: Callable[[str], None] = print,
    usage_reader: Callable[[], GpuUsage] = get_rocm_gpu_usage,
    sleeper: Callable[[float], None] = time.sleep,
) -> GpuUsage:
    """Wait until compute and VRAM usage are both below configured limits."""
    while True:
        usage = usage_reader()
        if usage.use_percent <= max_use_percent and usage.memory_percent <= max_memory_percent:
            return usage
        logger(
            "GPU busy "
            f"(use={usage.use_percent}%, memory={usage.memory_percent}%); "
            f"retrying in {poll_seconds}s"
        )
        sleeper(poll_seconds)
