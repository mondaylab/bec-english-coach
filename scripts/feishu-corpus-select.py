#!/usr/bin/env python3
"""Select one BEC exercise from the Feishu Base corpus."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any


BASE_TOKEN = "Ngg7bXz2SaxJgLsfPBAc3sTjngg"
CORPUS_TABLE = "tblNtU5HPcYrVshR"
FIELDS = [
    "语料 ID",
    "BEC Part",
    "一级主题",
    "细分主题",
    "英文题目",
    "中文题意",
    "答题要点",
    "原始参考答案",
    "清洗后参考答案",
    "可复用表达",
    "难度",
    "质量状态",
    "使用次数",
    "最近使用",
    "来源链接",
]


def run_lark(args: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        ["lark-cli", *args, "--as", "user", "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(result.stderr or result.stdout or str(exc)) from exc
    if result.returncode != 0 or not payload.get("ok"):
        raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    return payload


def query(part: str, theme: str, enabled: bool) -> list[dict[str, Any]]:
    conditions: list[list[Any]] = [
        ["语料 ID", "non_empty"],
        ["BEC Part", "intersects", [part]],
        ["一级主题", "intersects", [theme]],
    ]
    if enabled:
        conditions.append(["启用", "==", True])
    else:
        conditions.extend([["启用", "==", False], ["质量状态", "intersects", ["待清洗"]]])
    args = [
        "base",
        "+record-list",
        "--base-token",
        BASE_TOKEN,
        "--table-id",
        CORPUS_TABLE,
        "--filter-json",
        json.dumps({"logic": "and", "conditions": conditions}, ensure_ascii=False),
        "--sort-json",
        json.dumps(
            [
                {"field": "使用次数", "desc": False},
                {"field": "最近使用", "desc": False},
                {"field": "语料 ID", "desc": False},
            ],
            ensure_ascii=False,
        ),
        "--limit",
        "1",
    ]
    for field in FIELDS:
        args.extend(["--field-id", field])
    payload = run_lark(args)
    data = payload["data"]
    if not data.get("record_id_list"):
        return []
    return [
        {
            "record_id": data["record_id_list"][0],
            "fields": dict(zip(data["fields"], data["data"][0])),
        }
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--part", required=True, choices=["Part 1", "Part 2", "Part 3"])
    parser.add_argument("--theme", required=True)
    args = parser.parse_args()

    selected = query(args.part, args.theme, enabled=True)
    needs_curation = False
    if not selected:
        selected = query(args.part, args.theme, enabled=False)
        needs_curation = bool(selected)
    result = {
        "ok": bool(selected),
        "needs_curation": needs_curation,
        "record": selected[0] if selected else None,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if selected else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
