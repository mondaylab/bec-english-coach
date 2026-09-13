#!/usr/bin/env python3
"""Sync the BEC source corpus from Feishu Wiki/Mindnotes into Feishu Base."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Iterable


THEMES = [
    ("公司运作与管理", "E5ZVbaxDCmGtVxnlauFcBAJjn1d", "IEYbb9xdYmMFvWn1Q0IceK8FnIf"),
    ("人力资源管理", "ER3obapPEmgpwona3FocWv48nBc", "bmncnhqvBVBCqE6a6VeA7u8NAQg"),
    ("市场营销", "VYczbnpmNmwKhonK5IBcLPoUnlh", "bmncnAsfqhclq4TCvOAzJVVJa4f"),
    ("商务旅行", "KeIJbUdgSmCBXjngQPvc3p60n4c", "KlUKbagoOmh0henWhHhcyihUn6c"),
    ("企业公关", "Iwjzb2SpDmj0mrnfdj2c30s1nYe", "BjZ1bNbL7mH0zDnzwORcbWhdnTh"),
    ("公司发展", "Ts8QbyUGsmAQ3XnKVeScCvUnn4e", "Vlf0bzL9Nm6hm3ntscYcTzwynBg"),
    ("电子商务与新型工作方式", "GjhXbWsy9mzMjwnj8QBc9Fa6nSb", "EJjNbbHjKmeFLinQIpocOqdCnlc"),
    ("商务会议", "PEC1bXQECmQigenJ9PZckbb7nYe", "TaK0btSkmmjVv4nP4cTcCHVMnHe"),
]

PART3_WIKI_TOKEN = "XsDJwPYTji0ISskUEnDcCampnyh"
PART3_WIKI_URL = f"https://my.feishu.cn/wiki/{PART3_WIKI_TOKEN}"
MANAGED_FIELDS = [
    "语料 ID",
    "BEC Part",
    "一级主题",
    "细分主题",
    "英文题目",
    "中文题意",
    "答题要点",
    "原始参考答案",
    "来源类型",
    "来源链接",
    "来源节点",
    "来源版本",
    "标签",
]


@dataclass(frozen=True)
class SourceRecord:
    corpus_id: str
    fields: dict[str, Any]


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


def text_of(node: dict[str, Any]) -> str:
    return "".join(
        item.get("text", {}).get("content", "")
        for item in node.get("texts", [])
        if item.get("element_type") == "text"
    ).strip()


def notes_of(node: dict[str, Any]) -> str:
    return "\n".join(
        item.get("text", {}).get("content", "").strip()
        for item in node.get("notes", [])
        if item.get("element_type") == "text" and item.get("text", {}).get("content", "").strip()
    )


def node_tree(nodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    children: dict[str, list[dict[str, Any]]] = {}
    roots: list[dict[str, Any]] = []
    for node in nodes:
        parent_id = node.get("parent_id")
        if parent_id:
            children.setdefault(parent_id, []).append(node)
        else:
            roots.append(node)
    return roots, children


def descendants(node_id: str, children: dict[str, list[dict[str, Any]]]) -> Iterable[dict[str, Any]]:
    for child in children.get(node_id, []):
        yield child
        yield from descendants(child["node_id"], children)


def content_hash(*values: str) -> str:
    content = "\n".join(values).encode("utf-8")
    return hashlib.sha256(content).hexdigest()[:16]


def clean_heading(value: str) -> str:
    return re.sub(r"^\s*\d+(?:\.\d+)?[.)]?\s*", "", value).strip()


def strip_answer_prefix(value: str) -> str:
    return re.sub(r"^\s*(?:论点\s*\d+)[^:：]{0,4}[:：]\s*", "", value).strip()


def base_fields(
    *,
    corpus_id: str,
    part: str,
    theme: str,
    subtopic: str,
    question: str,
    chinese: str,
    points: str,
    answer: str,
    source_type: str,
    source_url: str,
    source_node: str,
    source_version: str,
    notes: str = "",
) -> SourceRecord:
    fields: dict[str, Any] = {
        "语料 ID": corpus_id,
        "BEC Part": [part],
        "一级主题": [theme],
        "细分主题": subtopic,
        "英文题目": question,
        "中文题意": chinese,
        "答题要点": points,
        "原始参考答案": answer,
        "难度": ["Level 1"],
        "质量状态": ["待清洗"],
        "来源类型": [source_type],
        "来源链接": source_url,
        "来源节点": source_node,
        "来源版本": source_version,
        "标签": subtopic,
        "启用": False,
        "备注": notes,
    }
    return SourceRecord(corpus_id=corpus_id, fields=fields)


def fetch_mindnote(token: str) -> list[dict[str, Any]]:
    payload = run_lark(["mindnotes", "nodes", "list", "--mindnote-id", token])
    return payload["data"]["nodes"]


def parse_part1(theme_index: int, theme: str, token: str) -> list[SourceRecord]:
    nodes = fetch_mindnote(token)
    roots, children = node_tree(nodes)
    records: list[SourceRecord] = []
    for root in roots:
        subtopic = clean_heading(text_of(root))
        for question_node in children.get(root["node_id"], []):
            question = text_of(question_node)
            if not question:
                continue
            answer_nodes = list(descendants(question_node["node_id"], children))
            answer_parts = [text_of(node) for node in answer_nodes if text_of(node)]
            answer = "\n\n".join(answer_parts)
            vocabulary_notes = [notes_of(node) for node in answer_nodes if notes_of(node)]
            corpus_id = f"P1-{theme_index:02d}-{question_node['node_id']}"
            records.append(
                base_fields(
                    corpus_id=corpus_id,
                    part="Part 1",
                    theme=theme,
                    subtopic=subtopic,
                    question=question,
                    chinese=notes_of(question_node),
                    points="",
                    answer=answer,
                    source_type="思维笔记",
                    source_url=f"https://my.feishu.cn/mindnotes/{token}",
                    source_node=f"{token}:{question_node['node_id']}",
                    source_version=content_hash(question, answer),
                    notes="\n".join(vocabulary_notes),
                )
            )
    return records


def parse_part2(theme_index: int, theme: str, token: str) -> list[SourceRecord]:
    nodes = fetch_mindnote(token)
    roots, children = node_tree(nodes)
    records: list[SourceRecord] = []
    for root in roots:
        root_children = children.get(root["node_id"], [])
        answer_marker = next((node for node in root_children if "suggested answer" in text_of(node).lower()), None)
        question_nodes = [node for node in root_children if node is not answer_marker and text_of(node)]
        if not question_nodes:
            continue
        question_node = question_nodes[0]
        question = text_of(question_node)
        if len(question_nodes) > 1:
            question = "\n".join(text_of(node) for node in question_nodes)
        point_nodes = list(descendants(question_node["node_id"], children))
        points = "\n".join(
            f"{text_of(node)}" + (f"（{notes_of(node)}）" if notes_of(node) else "")
            for node in point_nodes
            if text_of(node)
        )
        answer_nodes = list(descendants(answer_marker["node_id"], children)) if answer_marker else []
        answer = "\n\n".join(strip_answer_prefix(text_of(node)) for node in answer_nodes if text_of(node))
        vocab_notes = [notes_of(node) for node in answer_nodes if notes_of(node)]
        corpus_id = f"P2-{theme_index:02d}-{root['node_id']}"
        records.append(
            base_fields(
                corpus_id=corpus_id,
                part="Part 2",
                theme=theme,
                subtopic=clean_heading(text_of(root)),
                question=question,
                chinese=notes_of(question_node),
                points=points,
                answer=answer,
                source_type="思维笔记",
                source_url=f"https://my.feishu.cn/mindnotes/{token}",
                source_node=f"{token}:{root['node_id']}",
                source_version=content_hash(question, points, answer),
                notes="\n".join(vocab_notes),
            )
        )
    return records


def parse_part3() -> list[SourceRecord]:
    payload = run_lark(["docs", "+fetch", "--doc", PART3_WIKI_TOKEN, "--doc-format", "markdown"])
    document = payload["data"]["document"]
    content = document["content"]
    revision = str(document.get("revision_id", ""))
    section_pattern = re.compile(r"(?ms)^##\s+(\d+)\.(\d+)\s+(.+?)\n(.*?)(?=^##\s+\d+\.\d+\s+|^#\s+\d+\s+|\Z)")
    block_pattern = re.compile(r"```(?:Bash|bash)?\n(.*?)```", re.S)
    records: list[SourceRecord] = []
    for section in section_pattern.finditer(content):
        theme_index = int(section.group(1))
        if not 1 <= theme_index <= len(THEMES):
            continue
        body = section.group(4)
        blocks = [block.strip() for block in block_pattern.findall(body)]
        if not blocks or not blocks[0]:
            continue
        task = blocks[0]
        answer = blocks[1] if len(blocks) > 1 else ""
        if not answer:
            continue
        points = "\n".join(line.strip("▷ ") for line in task.splitlines() if line.strip().startswith("▷"))
        section_id = f"{section.group(1)}.{section.group(2)}"
        corpus_id = f"P3-{theme_index:02d}-{section_id.replace('.', '')}"
        records.append(
            base_fields(
                corpus_id=corpus_id,
                part="Part 3",
                theme=THEMES[theme_index - 1][0],
                subtopic=section.group(3).strip(),
                question=task,
                chinese="",
                points=points,
                answer=answer,
                source_type="Wiki 文档",
                source_url=PART3_WIKI_URL,
                source_node=f"{PART3_WIKI_TOKEN}:{section_id}",
                source_version=revision,
                notes="仅导入原文中任务和参考答案均非空的 Part 3 条目。",
            )
        )
    return records


def extract_records() -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for index, (theme, part1_token, part2_token) in enumerate(THEMES, start=1):
        records.extend(parse_part1(index, theme, part1_token))
        records.extend(parse_part2(index, theme, part2_token))
    records.extend(parse_part3())
    duplicate_ids = [item for item in {record.corpus_id for record in records} if sum(r.corpus_id == item for r in records) > 1]
    if duplicate_ids:
        raise RuntimeError(f"duplicate corpus IDs: {duplicate_ids}")
    return records


def existing_records(base_token: str, table_id: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    offset = 0
    while True:
        payload = run_lark(
            [
                "base",
                "+record-list",
                "--base-token",
                base_token,
                "--table-id",
                table_id,
                "--field-id",
                "语料 ID",
                "--limit",
                "200",
                "--offset",
                str(offset),
            ]
        )
        data = payload["data"]
        record_ids = data.get("record_id_list", [])
        for record_id, row in zip(record_ids, data.get("data", [])):
            if row and row[0]:
                mapping[str(row[0])] = record_id
        if not data.get("has_more") or not record_ids:
            break
        offset += len(record_ids)
    return mapping


def batched(items: list[Any], size: int = 100) -> Iterable[list[Any]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def sync_records(records: list[SourceRecord], base_token: str, table_id: str, dry_run: bool) -> tuple[int, int]:
    existing = existing_records(base_token, table_id)
    creates = [record.fields for record in records if record.corpus_id not in existing]
    updates = {
        existing[record.corpus_id]: {key: value for key, value in record.fields.items() if key in MANAGED_FIELDS}
        for record in records
        if record.corpus_id in existing
    }
    if dry_run:
        return len(creates), len(updates)
    for chunk in batched(creates):
        run_lark(
            [
                "base",
                "+record-batch-create",
                "--base-token",
                base_token,
                "--table-id",
                table_id,
                "--json",
                json.dumps({"create_records": chunk}, ensure_ascii=False),
            ]
        )
    for entries in batched(list(updates.items())):
        run_lark(
            [
                "base",
                "+record-batch-update",
                "--base-token",
                base_token,
                "--table-id",
                table_id,
                "--json",
                json.dumps({"update_records": dict(entries)}, ensure_ascii=False),
            ]
        )
    return len(creates), len(updates)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-token", required=True)
    parser.add_argument("--table-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    records = extract_records()
    counts: dict[str, int] = {}
    for record in records:
        part = record.fields["BEC Part"][0]
        counts[part] = counts.get(part, 0) + 1
    creates, updates = sync_records(records, args.base_token, args.table_id, args.dry_run)
    print(
        json.dumps(
            {"records": len(records), "by_part": counts, "creates": creates, "updates": updates, "dry_run": args.dry_run},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
