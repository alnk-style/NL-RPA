from __future__ import annotations

import pytest

from app.engine.transforms import apply_transform, split_lines


def test_split_lines_removes_blank_and_metadata() -> None:
    lines = split_lines(
        "[00:01.00]第一句\n\n[00:03.00]第二句\n"
    )

    assert lines == ["第一句", "第二句"]


def test_count_chars_returns_per_line_counts() -> None:
    result = apply_transform(
        "count_chars",
        {"values": ["第一句", "第二句"]},
        {},
    )

    assert result == [3, 3]


def test_list_transforms_preserve_order() -> None:
    values = ["  beta ", "", "alpha", "beta", "alphabet"]

    assert apply_transform("trim_lines", {"values": values}, {}) == [
        "beta",
        "",
        "alpha",
        "beta",
        "alphabet",
    ]
    assert apply_transform(
        "remove_blank_lines",
        {"values": values},
        {},
    ) == ["beta", "alpha", "beta", "alphabet"]
    assert apply_transform(
        "deduplicate",
        {"values": values, "trim": True},
        {},
    ) == ["beta", "", "alpha", "alphabet"]
    assert apply_transform(
        "filter_contains",
        {"values": values, "text": "alpha"},
        {},
    ) == ["alpha", "alphabet"]


def test_map_fields_and_merge_rows() -> None:
    rows = [
        {"name": "商品 A", "price": 10},
        {"name": "商品 B", "price": 20},
    ]

    mapped = apply_transform(
        "map_fields",
        {
            "values": rows,
            "mapping": {"name": "标题", "price": "价格"},
        },
        {},
    )
    merged = apply_transform(
        "merge_rows",
        {
            "values": [
                [{"id": 1}],
                [{"id": 2}],
            ]
        },
        {},
    )

    assert mapped == [
        {"标题": "商品 A", "价格": 10},
        {"标题": "商品 B", "价格": 20},
    ]
    assert merged == [{"id": 1}, {"id": 2}]


def test_aggregate_and_sort_rows() -> None:
    rows = [
        {"name": "A", "score": 3},
        {"name": "B", "score": 7},
        {"name": "C", "score": 5},
    ]

    assert apply_transform(
        "aggregate_sum",
        {"values": rows, "field": "score"},
        {},
    ) == 15
    assert apply_transform(
        "sort_by",
        {"values": rows, "field": "score", "reverse": True},
        {},
    ) == [
        {"name": "B", "score": 7},
        {"name": "C", "score": 5},
        {"name": "A", "score": 3},
    ]


def test_apply_transform_resolves_variable_inputs() -> None:
    result = apply_transform(
        "split_lines",
        {"text": "${lyrics}"},
        {"lyrics": "第一句\n第二句"},
    )

    assert result == ["第一句", "第二句"]


def test_apply_transform_rejects_unknown_action() -> None:
    with pytest.raises(ValueError, match="Unsupported transform"):
        apply_transform("eval_python", {"text": "1+1"}, {})
