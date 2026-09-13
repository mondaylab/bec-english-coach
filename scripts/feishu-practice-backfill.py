#!/usr/bin/env python3
"""Backfill local BEC practice summaries into the Feishu Base learning tables."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


BASE_TOKEN = "Ngg7bXz2SaxJgLsfPBAc3sTjngg"
CORPUS_TABLE = "tblNtU5HPcYrVshR"
EXPRESSION_TABLE = "tblk4SUij9GQnoRB"
TRAINING_TABLE = "tbl9izlf5aYGOWTH"
WEAKNESS_TABLE = "tbl8bP9oNLgOD9nC"
CORPUS_SOURCE_FIELDS = {
    "语料 ID",
    "BEC Part",
    "一级主题",
    "细分主题",
    "英文题目",
    "答题要点",
    "原始参考答案",
    "可复用表达",
    "来源类型",
    "来源节点",
    "来源版本",
    "标签",
}
TRAINING_SOURCE_FIELDS = {
    "训练 ID",
    "日期",
    "Day",
    "BEC Part",
    "一级主题",
    "Level",
    "用户回答",
    "升级版本",
    "最佳升级句",
    "使用语料",
    "飞书文档",
    "备注",
}


@dataclass(frozen=True)
class Practice:
    day: int
    date: str
    part: str
    theme: str
    question: str
    level: str
    focus: str
    answer: str
    upgraded: str
    expressions: tuple[str, ...]
    errors: tuple[str, ...]
    next_practice: str
    feishu_doc: str
    source_path: str


@dataclass(frozen=True)
class WeaknessRule:
    weakness_id: str
    weakness_type: str
    description: str
    improvement: str
    pattern: re.Pattern[str]


WEAKNESS_RULES = (
    WeaknessRule(
        "WEAK-001",
        "词汇与搭配",
        "拼写不稳会影响商务表达的专业感。",
        "复练前先检查高频商务词的拼写，再完整朗读答案。",
        re.compile(r"professionnal|decisioon|retroducing|llear|quiter", re.I),
    ),
    WeaknessRule(
        "WEAK-002",
        "词汇与搭配",
        "复合词和复合形容词的空格或连字符容易写错。",
        "记住 teamwork；名词前常用 check-in、work-life、follow-up。",
        re.compile(r"team work|check in meetings|work life balance|follow up", re.I),
    ),
    WeaknessRule(
        "WEAK-003",
        "语法",
        "名词单复数、冠词或主谓一致不稳定。",
        "说完后检查可数名词、复数结尾和主语对应的动词形式。",
        re.compile(r"client contacts|the misunderstanding|reduce risk|AI topic change|responsibilityon", re.I),
    ),
    WeaknessRule(
        "WEAK-004",
        "语法",
        "动词、名词和动名词之间的词性转换容易混淆。",
        "把能力写成 the ability to do；介词后和主语位置优先检查名词或动名词。",
        re.compile(r"motivate people|relevant is important|Respond to customers seriously|turn discuss into", re.I),
    ),
    WeaknessRule(
        "WEAK-005",
        "语法",
        "代词指代有时没有与前文的人或事保持一致。",
        "回看代词所指对象，区分 it、them、its 和 their。",
        re.compile(r"helps the communicate|values its opinions|achieve their results", re.I),
    ),
    WeaknessRule(
        "WEAK-006",
        "词汇与搭配",
        "个别词汇或搭配选择会改变原本的商业意思。",
        "优先使用已经验证过的商务搭配，并检查否定词是否遗漏。",
        re.compile(
            r"effort the team|work mostly|customer brand|strong pricing|long pricing|can afford|freelancer writers|is banned into",
            re.I,
        ),
    ),
    WeaknessRule(
        "WEAK-007",
        "结构",
        "话语标记后的标点、大小写和连接方式不够稳定。",
        "使用 First, For example, 和 Overall, 时固定检查逗号与后续小写。",
        re.compile(r"First customer|useful, competitive and clearly positioned|Okay, overall|Overall，|For example, If|first, Respond|Over all", re.I),
    ),
)


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


def section(markdown: str, title: str) -> str:
    match = re.search(rf"(?ms)^## {re.escape(title)}\s*\n(.*?)(?=^## |\Z)", markdown)
    return match.group(1).strip() if match else ""


def metadata(markdown: str, key: str) -> str:
    match = re.search(rf"(?m)^{re.escape(key)}：\s*(.+)$", markdown)
    return match.group(1).strip() if match else ""


def bullets(value: str) -> tuple[str, ...]:
    return tuple(
        match.group(1).strip()
        for match in re.finditer(r"(?m)^-\s+(.+)$", value)
        if match.group(1).strip()
    )


def parse_practice(path: Path, root: Path) -> Practice:
    markdown = path.read_text(encoding="utf-8")
    heading = re.search(r"(?m)^# Day (\d+) - BEC Part (\d) - (.+)$", markdown)
    if not heading:
        raise ValueError(f"unsupported practice heading: {path}")
    feishu_doc = re.search(r"(?m)^飞书文档：(https?://\S+)$", markdown)
    return Practice(
        day=int(heading.group(1)),
        date=metadata(markdown, "日期"),
        part=f"Part {heading.group(2)}",
        theme=metadata(markdown, "主题") or heading.group(3).strip(),
        question=metadata(markdown, "题目"),
        level=metadata(markdown, "辅助等级"),
        focus=metadata(markdown, "训练重点"),
        answer=section(markdown, "我的原始回答"),
        upgraded=section(markdown, "修改后的商务英语"),
        expressions=bullets(section(markdown, "今日表达")),
        errors=bullets(section(markdown, "今日错误")),
        next_practice=section(markdown, "下次复练"),
        feishu_doc=feishu_doc.group(1) if feishu_doc else "",
        source_path=str(path.relative_to(root.parent)),
    )


def load_practices(root: Path) -> list[Practice]:
    practices = [parse_practice(path, root) for path in sorted(root.glob("20*/*/*.md"))]
    ids = [item.day for item in practices]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate Day numbers in practice files")
    return practices


def list_records(table_id: str, fields: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        args = [
            "base",
            "+record-list",
            "--base-token",
            BASE_TOKEN,
            "--table-id",
            table_id,
            "--limit",
            "200",
            "--offset",
            str(offset),
        ]
        for field in fields:
            args.extend(["--field-id", field])
        data = run_lark(args)["data"]
        ids = data.get("record_id_list", [])
        for record_id, values in zip(ids, data.get("data", [])):
            records.append({"record_id": record_id, **dict(zip(data["fields"], values))})
        if not data.get("has_more") or not ids:
            break
        offset += len(ids)
    return records


def batched(items: list[Any], size: int = 100) -> Iterable[list[Any]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def create_records(table_id: str, records: list[dict[str, Any]]) -> list[str]:
    record_ids: list[str] = []
    for chunk in batched(records):
        payload = run_lark(
            [
                "base",
                "+record-batch-create",
                "--base-token",
                BASE_TOKEN,
                "--table-id",
                table_id,
                "--json",
                json.dumps({"create_records": chunk}, ensure_ascii=False),
            ]
        )
        record_ids.extend(payload["data"]["record_id_list"])
    return record_ids


def update_records(table_id: str, records: dict[str, dict[str, Any]]) -> None:
    for chunk in batched(list(records.items())):
        run_lark(
            [
                "base",
                "+record-batch-update",
                "--base-token",
                BASE_TOKEN,
                "--table-id",
                table_id,
                "--json",
                json.dumps({"update_records": dict(chunk)}, ensure_ascii=False),
            ]
        )


def link_ids(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        item.get("id") or item.get("record_id")
        for item in value
        if isinstance(item, dict) and (item.get("id") or item.get("record_id"))
    }


def corpus_id(practice: Practice) -> str:
    return f"AI-DAY-{practice.day:03d}"


def training_id(practice: Practice) -> str:
    return f"TRAIN-DAY-{practice.day:03d}"


def corpus_fields(practice: Practice) -> dict[str, Any]:
    return {
        "语料 ID": corpus_id(practice),
        "BEC Part": [practice.part],
        "一级主题": [practice.theme],
        "细分主题": practice.focus,
        "英文题目": practice.question,
        "答题要点": practice.focus,
        "原始参考答案": practice.upgraded,
        "清洗后参考答案": practice.upgraded,
        "可复用表达": "\n".join(practice.expressions),
        "难度": [practice.level],
        "质量状态": ["已确认"],
        "来源类型": ["AI 生成"],
        "来源节点": practice.source_path,
        "来源版本": hashlib.sha256(practice.upgraded.encode("utf-8")).hexdigest()[:16],
        "标签": practice.focus,
        "使用次数": 1,
        "最近使用": practice.date,
        "启用": True,
        "备注": "由历史练习摘要回填；已完成一次训练。",
    }


def best_sentence(answer: str) -> str:
    compact = re.sub(r"\s+", " ", answer).strip()
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", compact) if item.strip()]
    return next((item for item in sentences if item.lower().startswith("overall,")), sentences[-1] if sentences else "")


def training_fields(practice: Practice, source_record_id: str) -> dict[str, Any]:
    notes = [f"训练重点：{practice.focus}"]
    if practice.errors:
        notes.append("今日错误：\n" + "\n".join(f"- {item}" for item in practice.errors))
    if practice.next_practice:
        notes.append("下次复练：" + practice.next_practice)
    fields: dict[str, Any] = {
        "训练 ID": training_id(practice),
        "日期": practice.date,
        "Day": practice.day,
        "BEC Part": [practice.part],
        "一级主题": [practice.theme],
        "Level": [practice.level],
        "用户回答": practice.answer,
        "升级版本": practice.upgraded,
        "最佳升级句": best_sentence(practice.upgraded),
        "使用语料": [{"id": source_record_id}],
        "训练状态": ["已完成"],
        "是否复练": False,
        "备注": "\n\n".join(notes),
    }
    if practice.feishu_doc:
        fields["飞书文档"] = practice.feishu_doc
    return fields


def sync_corpus(practices: list[Practice], dry_run: bool) -> tuple[dict[int, str], dict[str, int]]:
    existing = {
        row["语料 ID"]: row["record_id"]
        for row in list_records(CORPUS_TABLE, ["语料 ID"])
        if row.get("语料 ID")
    }
    creates = [practice for practice in practices if corpus_id(practice) not in existing]
    updates = [practice for practice in practices if corpus_id(practice) in existing]
    if not dry_run:
        new_ids = create_records(CORPUS_TABLE, [corpus_fields(item) for item in creates])
        existing.update({corpus_id(item): record_id for item, record_id in zip(creates, new_ids)})
        update_records(
            CORPUS_TABLE,
            {
                existing[corpus_id(item)]: {
                    key: value
                    for key, value in corpus_fields(item).items()
                    if key in CORPUS_SOURCE_FIELDS
                }
                for item in updates
            },
        )
    return (
        {item.day: existing.get(corpus_id(item), f"new:{corpus_id(item)}") for item in practices},
        {"creates": len(creates), "updates": len(updates)},
    )


def sync_training(
    practices: list[Practice], corpus_records: dict[int, str], dry_run: bool
) -> tuple[dict[int, str], dict[str, int]]:
    existing = {
        row["训练 ID"]: row["record_id"]
        for row in list_records(TRAINING_TABLE, ["训练 ID"])
        if row.get("训练 ID")
    }
    creates = [item for item in practices if training_id(item) not in existing]
    updates = [item for item in practices if training_id(item) in existing]
    if not dry_run:
        new_ids = create_records(
            TRAINING_TABLE,
            [training_fields(item, corpus_records[item.day]) for item in creates],
        )
        existing.update({training_id(item): record_id for item, record_id in zip(creates, new_ids)})
        update_records(
            TRAINING_TABLE,
            {
                existing[training_id(item)]: {
                    key: value
                    for key, value in training_fields(item, corpus_records[item.day]).items()
                    if key in TRAINING_SOURCE_FIELDS
                }
                for item in updates
            },
        )
    return (
        {item.day: existing.get(training_id(item), f"new:{training_id(item)}") for item in practices},
        {"creates": len(creates), "updates": len(updates)},
    )


def normal_expression(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def expression_id(value: str) -> str:
    digest = hashlib.sha1(normal_expression(value).encode("utf-8")).hexdigest()[:10].upper()
    return f"HIST-{digest}"


def containing_sentence(answer: str, expression: str) -> str:
    compact = re.sub(r"\s+", " ", answer).strip()
    for sentence in re.split(r"(?<=[.!?])\s+", compact):
        if normal_expression(expression).rstrip(".") in normal_expression(sentence):
            return sentence.strip()
    return expression


def sync_expressions(
    practices: list[Practice], corpus_records: dict[int, str], dry_run: bool
) -> dict[str, int]:
    grouped: dict[str, list[tuple[Practice, str]]] = defaultdict(list)
    for practice in practices:
        for expression in practice.expressions:
            grouped[normal_expression(expression)].append((practice, expression))

    existing_rows = list_records(
        EXPRESSION_TABLE,
        ["英文表达", "BEC Part", "一级主题", "来源语料", "使用次数", "最近使用"],
    )
    existing = {
        normal_expression(row["英文表达"]): row
        for row in existing_rows
        if row.get("英文表达")
    }
    creates: list[dict[str, Any]] = []
    updates: dict[str, dict[str, Any]] = {}
    for key, occurrences in grouped.items():
        latest_practice, display = max(occurrences, key=lambda item: item[0].date)
        themes = sorted({item.theme for item, _ in occurrences})
        parts = sorted({item.part for item, _ in occurrences})
        source_ids = sorted({corpus_records[item.day] for item, _ in occurrences})
        current = existing.get(key)
        current_links = link_ids((current or {}).get("来源语料", []))
        fields: dict[str, Any] = {
            "英文表达": display,
            "BEC Part": sorted(set(parts) | set((current or {}).get("BEC Part", []))),
            "一级主题": sorted(set(themes) | set((current or {}).get("一级主题", []))),
            "来源语料": [{"id": item} for item in sorted(set(source_ids) | current_links)],
            "使用次数": max(len(occurrences), int((current or {}).get("使用次数") or 0)),
            "最近使用": max(latest_practice.date, str((current or {}).get("最近使用") or "")[:10]),
        }
        if current:
            updates[current["record_id"]] = fields
        else:
            fields.update(
                {
                    "表达 ID": expression_id(display),
                    "功能": ["总结" if display.lower().startswith("overall") else "其他"],
                    "例句": containing_sentence(latest_practice.upgraded, display),
                    "质量状态": ["已确认"],
                    "掌握状态": ["学习中"],
                    "备注": "由历史练习中的今日表达回填。",
                }
            )
            creates.append(fields)
    if not dry_run:
        create_records(EXPRESSION_TABLE, creates)
        update_records(EXPRESSION_TABLE, updates)
    return {"creates": len(creates), "updates": len(updates)}


def sync_weaknesses(
    practices: list[Practice], training_records: dict[int, str], dry_run: bool
) -> dict[str, int]:
    grouped: dict[str, list[tuple[Practice, str]]] = defaultdict(list)
    rules = {rule.weakness_id: rule for rule in WEAKNESS_RULES}
    for practice in practices:
        for error in practice.errors:
            rule = next((item for item in WEAKNESS_RULES if item.pattern.search(error)), None)
            if rule:
                grouped[rule.weakness_id].append((practice, error))
    grouped = {key: value for key, value in grouped.items() if len(value) >= 2}

    existing = {
        row["薄弱点 ID"]: row
        for row in list_records(
            WEAKNESS_TABLE,
            ["薄弱点 ID", "出现次数", "最近出现", "推荐主题", "关联训练"],
        )
        if row.get("薄弱点 ID")
    }
    creates: list[dict[str, Any]] = []
    updates: dict[str, dict[str, Any]] = {}
    for weakness_id, occurrences in grouped.items():
        rule = rules[weakness_id]
        current = existing.get(weakness_id)
        current_links = link_ids((current or {}).get("关联训练", []))
        current_themes = set((current or {}).get("推荐主题", []))
        source_links = {training_records[item.day] for item, _ in occurrences}
        fields = {
            "薄弱点 ID": weakness_id,
            "类型": [rule.weakness_type],
            "描述": rule.description,
            "错误示例": "\n".join(f"Day {item.day:03d}: {error}" for item, error in occurrences),
            "改进表达": rule.improvement,
            "出现次数": max(len(occurrences), int((current or {}).get("出现次数") or 0)),
            "最近出现": max(
                max(item.date for item, _ in occurrences),
                str((current or {}).get("最近出现") or "")[:10],
            ),
            "推荐主题": sorted(current_themes | {item.theme for item, _ in occurrences}),
            "关联训练": [
                {"id": record_id}
                for record_id in sorted(current_links | source_links)
            ],
            "备注": "仅聚合在历史记录中重复出现的错误模式。",
        }
        if current:
            updates[current["record_id"]] = fields
        else:
            fields["掌握状态"] = ["练习中"]
            creates.append(fields)
    if not dry_run:
        create_records(WEAKNESS_TABLE, creates)
        update_records(WEAKNESS_TABLE, updates)
    return {"creates": len(creates), "updates": len(updates)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--practice-root", type=Path, default=Path("practice"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    practices = load_practices(args.practice_root)
    corpus_records, corpus_stats = sync_corpus(practices, args.dry_run)
    training_records, training_stats = sync_training(practices, corpus_records, args.dry_run)
    expression_stats = sync_expressions(practices, corpus_records, args.dry_run)
    weakness_stats = sync_weaknesses(practices, training_records, args.dry_run)
    print(
        json.dumps(
            {
                "practices": len(practices),
                "corpus": corpus_stats,
                "training": training_stats,
                "expressions": expression_stats,
                "weaknesses": weakness_stats,
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
