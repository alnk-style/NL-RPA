from __future__ import annotations

import asyncio

from app.actions.validation import validate_workflow_draft


def test_validation_rejects_unknown_variable_domain_and_high_risk_gap() -> None:
    unknown_variable = validate_workflow_draft(
        {
            "execution_mode": "structured",
            "variables": [],
            "steps": [
                {
                    "step_id": 1,
                    "executor": "transform",
                    "action": "limit",
                    "params": {
                        "values": "${missing_rows}",
                        "count": 10,
                    },
                }
            ],
        }
    )
    assert unknown_variable.valid is False
    assert any("unknown variable" in item.lower() for item in unknown_variable.errors)

    out_of_domain = validate_workflow_draft(
        {
            "execution_mode": "structured",
            "crawl_policy": {
                "allowed_domains": ["example.com"],
                "max_pages": 2,
                "max_items": 10,
            },
            "steps": [
                {
                    "step_id": 1,
                    "executor": "web",
                    "action": "goto",
                    "params": {"url": "https://other.example.net"},
                }
            ],
        }
    )
    assert out_of_domain.valid is False
    assert any("domain" in item.lower() for item in out_of_domain.errors)


def test_validation_accepts_declared_variables_and_generated_results() -> None:
    result = validate_workflow_draft(
        {
            "execution_mode": "structured",
            "variables": [{"name": "rows", "default": []}],
            "steps": [
                {
                    "step_id": 1,
                    "executor": "transform",
                    "action": "filter",
                    "params": {
                        "values": "${rows}",
                        "operator": "gt",
                        "field": "amount",
                        "value": 1000,
                        "result_var": "large_rows",
                    },
                },
                {
                    "step_id": 2,
                    "executor": "transform",
                    "action": "limit",
                    "params": {
                        "values": "${large_rows}",
                        "count": 10,
                    },
                    "depends_on": [1],
                },
            ],
        }
    )
    assert result.valid is True
