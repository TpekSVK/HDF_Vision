"""Helpers for routing and aggregating multi-view execution."""

from collections.abc import Iterable, Mapping
from typing import Any

from app.models.schema import RecipeAggregation


def aggregate_branching_statuses(
    aggregation: RecipeAggregation,
    statuses: Mapping[str, str | None],
    ignored_view_ids: Iterable[str] | None = None,
) -> str:
    """Aggregate statuses while ignoring routing-only views.

    Args:
        aggregation: Recipe aggregation settings.
        statuses: Collected per-view statuses keyed by view ID.
        ignored_view_ids: View IDs that should be excluded from aggregation
            (e.g., branching router views).

    Returns:
        Aggregated status string using the provided aggregation mode.
    """

    ignored = {str(view_id) for view_id in (ignored_view_ids or []) if view_id}
    relevant_statuses: dict[str, Any] = {
        view_id: status for view_id, status in statuses.items() if view_id not in ignored
    }
    return aggregation.aggregate_statuses(relevant_statuses)

