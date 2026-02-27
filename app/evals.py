"""Evaluation helpers and SLO gating for phased rollout."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass
class EvalTrace:
    """Single evaluation trace result."""

    phase: int
    case_id: str
    tool_requested: bool
    tool_success: bool
    critical_failure: bool


@dataclass
class EvalMetrics:
    """Aggregated evaluation metrics."""

    total_cases: int
    tool_success_rate: float
    critical_failure_rate: float


def summarize_metrics(traces: Iterable[EvalTrace]) -> EvalMetrics:
    """Computes tool success and critical failure rates."""
    trace_list = list(traces)
    if not trace_list:
        return EvalMetrics(total_cases=0, tool_success_rate=0.0, critical_failure_rate=1.0)

    requested = [trace for trace in trace_list if trace.tool_requested]
    successful = [trace for trace in requested if trace.tool_success]
    critical = [trace for trace in trace_list if trace.critical_failure]

    tool_success_rate = (len(successful) / len(requested)) if requested else 1.0
    critical_failure_rate = len(critical) / len(trace_list)

    return EvalMetrics(
        total_cases=len(trace_list),
        tool_success_rate=tool_success_rate,
        critical_failure_rate=critical_failure_rate,
    )


def phase_gate(
    metrics: EvalMetrics,
    min_tool_success_rate: float = 0.80,
    max_critical_failure_rate: float = 0.05,
    observed_days: int = 14,
) -> tuple[bool, list[str]]:
    """Implements phase scaling policy from the plan."""
    reasons: list[str] = []

    if metrics.tool_success_rate < min_tool_success_rate:
        reasons.append(
            f"tool_success_rate={metrics.tool_success_rate:.2%} < {min_tool_success_rate:.2%}"
        )
    if metrics.critical_failure_rate > max_critical_failure_rate:
        reasons.append(
            f"critical_failure_rate={metrics.critical_failure_rate:.2%} > {max_critical_failure_rate:.2%}"
        )
    if observed_days < 14:
        reasons.append(f"observed_days={observed_days} < 14")

    return len(reasons) == 0, reasons
