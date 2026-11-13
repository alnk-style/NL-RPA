from __future__ import annotations

import asyncio

import pytest

from app.actions.data import execute_data_action
from app.actions.protocol import StepStatus
from app.actions.registry import default_action_registry


def _run(action: str, params: dict, variables: dict | None = None):
    return asyncio.run(
        execute_data_action(action, params, variables or {})
    )


def test_filter_amount_gt_and_status_and_nested_field() -> None:
    rows = [
        {"amount": "1200", "status": "active", "user": {"level": "gold"}},
        {"amount": "700", "status": "active", "user": {"level": "gold"}},
        {"amount": "1500", "status": "closed", "user": {"level": "gold"}},
        {"amount": "2000", "status": "active", "user": {"level": "silver"}},
    ]
    amount = _run(
        "filter",
        {
            "values": rows,
            "field": "amount",
            "operator": "gt",
            "cast": "number",
            "value": 1000,
            "allow_empty": False,
        },
    )
    assert amount.status is StepStatus.SUCCESS
    assert [row["amount"] for row in amount.value] == ["1200", "1500", "2000"]
    assert amount.verification["input_count"] == 4
    assert amount.verification["output_count"] == 3

    active = _run(
        "filter",
        {
            "values": rows,
            "field": "status",
            "operator": "eq",
            "value": "active",
            "allow_empty": False,
        },
    )
    assert len(active.value) == 3

    nested = _run(
        "filter",
        {
            "values": rows,
            "field": "user.level",
            "operator": "eq",
            "value": "gold",
            "allow_empty": False,
        },
    )
    assert len(nested.value) == 3


def test_filter_missing_field_and_empty_result_are_explicit() -> None:
    missing = _run(
        "filter",
        {
            "values": [{"name": "one"}],
            "field": "amount",
            "operator": "gt",
            "value": 1000,
        },
    )
    assert missing.status is StepStatus.FAILED
    assert missing.error is not None
    assert missing.error.category == "data_empty"

    allowed = _run(
        "filter",
        {
            "values": [{"name": "one"}],
            "field": "amount",
            "operator": "gt",
            "value": 1000,
            "allow_empty": True,
        },
    )
    assert allowed.status is StepStatus.SUCCESS
    assert allowed.value == []


def test_map_cast_sort_and_deduplicate() -> None:
    mapped = _run(
        "map",
        {
            "values": [{"first": "A", "last": "B", "amount": "12.5"}],
            "mapping": {
                "first": "given_name",
                "last": "family_name",
                "amount": "total",
            },
            "strict": False,
        },
    )
    assert mapped.value == [
        {"given_name": "A", "family_name": "B", "total": "12.5"}
    ]

    cast = _run(
        "cast",
        {
            "values": [{"amount": "12.5"}, {"amount": "bad"}],
            "field": "amount",
            "target": "number",
            "on_error": "skip",
        },
    )
    assert cast.value == [{"amount": 12.5}]

    sorted_rows = _run(
        "sort",
        {
            "values": [
                {"name": "missing"},
                {"name": "b", "score": 2},
                {"name": "a", "score": 1},
            ],
            "field": "score",
            "order": "asc",
            "missing": "last",
        },
    )
    assert [row["name"] for row in sorted_rows.value] == [
        "a",
        "b",
        "missing",
    ]

    deduplicated = _run(
        "deduplicate",
        {
            "values": [
                {"id": 1, "value": "first"},
                {"id": 1, "value": "last"},
                {"id": 2, "value": "other"},
            ],
            "keys": ["id"],
            "keep": "last",
        },
    )
    assert deduplicated.value == [
        {"id": 1, "value": "last"},
        {"id": 2, "value": "other"},
    ]


def test_cast_explicit_type_errors_and_dates() -> None:
    cast = _run(
        "cast",
        {
            "values": [{"date": "2026-10-28"}],
            "field": "date",
            "target": "date",
            "on_error": "error",
        },
    )
    assert cast.value == [{"date": "2026-10-28"}]

    failed = _run(
        "cast",
        {
            "values": [{"amount": "not-a-number"}],
            "field": "amount",
            "target": "number",
            "on_error": "error",
        },
    )
    assert failed.status is StepStatus.FAILED
    assert failed.error is not None
    assert failed.error.category == "business_validation_failed"


def test_aggregate_join_flatten_limit_and_validate() -> None:
    aggregate = _run(
        "aggregate",
        {
            "values": [
                {"team": "a", "amount": "10"},
                {"team": "a", "amount": "20"},
                {"team": "b", "amount": "5"},
            ],
            "operation": "sum",
            "field": "amount",
            "group_by": "team",
        },
    )
    assert aggregate.value == [
        {"group": "a", "value": 30},
        {"group": "b", "value": 5},
    ]

    joined = _run(
        "join",
        {
            "left": [{"id": 1, "left": "L1"}, {"id": 2, "left": "L2"}],
            "right": [{"id": 1, "right": "R1"}],
            "left_key": "id",
            "right_key": "id",
            "how": "left",
        },
    )
    assert joined.value == [
        {"id": 1, "left": "L1", "right": "R1"},
        {"id": 2, "left": "L2"},
    ]

    flattened = _run(
        "flatten",
        {
            "values": [
                {"tags": ["a", "b"]},
                {"tags": ["c"]},
                {"tags": []},
            ],
            "field": "tags",
            "depth": 1,
        },
    )
    assert flattened.value == ["a", "b", "c"]

    limited = _run(
        "limit",
        {"values": [1, 2, 3, 4], "count": 2, "offset": 1},
    )
    assert limited.value == [2, 3]

    valid = _run(
        "validate",
        {
            "values": [
                {"id": 1, "name": "A"},
                {"id": 2, "name": "B"},
            ],
            "required_fields": ["id", "name"],
            "min_rows": 2,
            "unique_by": ["id"],
        },
    )
    assert valid.status is StepStatus.SUCCESS
    assert len(valid.value) == 2


def test_validate_rejects_required_unique_and_row_count_failures() -> None:
    missing = _run(
        "validate",
        {
            "values": [{"id": 1}],
            "required_fields": ["id", "name"],
        },
    )
    assert missing.status is StepStatus.FAILED
    assert missing.error is not None
    assert missing.error.category == "business_validation_failed"

    duplicate = _run(
        "validate",
        {
            "values": [{"id": 1}, {"id": 1}],
            "unique_by": ["id"],
        },
    )
    assert duplicate.status is StepStatus.FAILED

    too_few = _run(
        "validate",
        {"values": [{"id": 1}], "min_rows": 2},
    )
    assert too_few.status is StepStatus.FAILED


@pytest.mark.parametrize(
    "action,params",
    [
        ("filter", {"values": [], "operator": "eq", "value": "x"}),
        ("map", {"values": [], "mapping": {}}),
        ("cast", {"values": [], "target": "string"}),
        ("sort", {"values": []}),
        ("deduplicate", {"values": []}),
        ("join", {"left": [], "right": [], "left_key": "id", "right_key": "id"}),
        ("flatten", {"values": []}),
        ("limit", {"values": [], "count": 1}),
        ("validate", {"values": []}),
    ],
)
def test_data_actions_reject_empty_by_default(
    action: str,
    params: dict,
) -> None:
    result = _run(action, params)
    assert result.status is StepStatus.FAILED
    assert result.error is not None
    assert result.error.category == "data_empty"


def test_result_variable_is_returned_and_catalog_registers_all_actions() -> None:
    result = _run(
        "limit",
        {
            "values": [{"id": 1}],
            "count": 1,
            "result_var": "large_transactions",
        },
    )
    assert result.variables["large_transactions"] == [{"id": 1}]

    catalog = default_action_registry().catalog(executor="transform")
    ids = {item.id for item in catalog.actions}
    assert ids == {
        "filter",
        "map",
        "cast",
        "sort",
        "deduplicate",
        "aggregate",
        "join",
        "flatten",
        "limit",
        "validate",
    }
