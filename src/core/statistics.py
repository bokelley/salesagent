"""Shared statistical helpers."""

from __future__ import annotations


def percentile(sorted_values: list[float], percentile_value: int) -> float | None:
    """Calculate a percentile from an already-sorted list using linear interpolation."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]

    index = (percentile_value / 100) * (len(sorted_values) - 1)
    lower_index = int(index)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    weight = index - lower_index
    return sorted_values[lower_index] * (1 - weight) + sorted_values[upper_index] * weight
