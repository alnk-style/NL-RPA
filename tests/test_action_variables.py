from __future__ import annotations

import pytest

from app.actions.conditions import evaluate_condition_spec
from app.actions.variables import VariableScope, resolve_template


def test_variable_scope_resolves_nested_paths_and_preserves_types() -> None:
    scope = VariableScope(
        {
            "transactions": {
                "rows": [{"amount": "1200"}, {"amount": "50"}]
            },
            "subject": "report",
        }
    )
    assert scope.get("transactions.rows") == [
        {"amount": "1200"},
        {"amount": "50"},
    ]
    assert resolve_template("${transactions.rows}", scope.values) == [
        {"amount": "1200"},
        {"amount": "50"},
    ]
    assert resolve_template("report: ${subject}", scope.values) == "report: report"


def test_variable_scope_rejects_unknown_paths() -> None:
    scope = VariableScope({"one": 1})
    with pytest.raises(ValueError, match="missing"):
        scope.get("missing.path")


def test_declarative_condition_supports_cast_and_boolean_groups() -> None:
    variables = {
        "transactions": {"amount": "1200", "name": "Alpha Report"},
        "rows": [1, 2, 3],
    }
    condition = {
        "all": [
            {
                "left": {"var": "transactions.amount"},
                "operator": "gt",
                "right": {"literal": 1000},
                "cast": "number",
            },
            {
                "any": [
                    {
                        "left": {"var": "transactions.name"},
                        "operator": "matches",
                        "right": {"literal": r"^Alpha"},
                    },
                    {
                        "left": {"var": "rows"},
                        "operator": "in",
                        "right": {"literal": [9]},
                    },
                ]
            },
            {
                "not": {
                    "left": {"var": "transactions.amount"},
                    "operator": "lt",
                    "right": {"literal": 0},
                    "cast": "number",
                }
            },
        ]
    }
    assert evaluate_condition_spec(condition, variables) is True


def test_condition_supports_startswith_endswith_and_not_in() -> None:
    variables = {"name": "invoice-2026.csv", "status": "ready"}
    assert evaluate_condition_spec(
        {
            "all": [
                {
                    "left": {"var": "name"},
                    "operator": "startswith",
                    "right": {"literal": "invoice-"},
                },
                {
                    "left": {"var": "name"},
                    "operator": "endswith",
                    "right": {"literal": ".csv"},
                },
                {
                    "left": {"var": "status"},
                    "operator": "not_in",
                    "right": {"literal": ["failed", "cancelled"]},
                },
            ]
        },
        variables,
    )
