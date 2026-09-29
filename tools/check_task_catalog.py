"""校验 68 项任务目录、Markdown 卡片标记和关键字段的一致性。"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


CARD_FIELDS = (
    "课程依据",
    "任务目标",
    "前置任务",
    "允许修改",
    "禁止修改",
    "输入输出",
    "公共接口",
    "实现步骤",
    "预期代码结构解释",
    "实现提示词",
    "测试用例",
    "详细验收标准",
    "独立验收提示词",
    "代码讲解提示词",
    "理解考试与小修改",
    "交付文件",
)
REQUIRED_TASK_FIELDS = frozenset(
    {
        "id",
        "member",
        "title",
        "kind",
        "module",
        "course_basis",
        "depends",
        "authorized_paths",
        "forbidden_paths",
        "test_path",
        "test_command",
        "function_group",
        "integration_input",
        "deliverables",
        "card",
    }
)


def expected_task_ids() -> set[str]:
    ids = {f"{member}-B{number:02d}" for member in "ABCD" for number in range(1, 7)}
    extension_counts = {"A": 10, "B": 11, "C": 12, "D": 11}
    ids.update(
        f"{member}-E{number:02d}"
        for member, count in extension_counts.items()
        for number in range(1, count + 1)
    )
    return ids


def _field_line_index(lines: list[str], field: str) -> int | None:
    forms = {field, f"**{field}**", f"{field}：", f"**{field}：**"}
    for index, line in enumerate(lines):
        normalized = line.strip().lstrip("#").strip()
        if normalized in forms:
            return index
    return None


def validate_catalog(repo_root: Path, catalog_path: Path | None = None) -> list[str]:
    problems: list[str] = []
    path = catalog_path or repo_root / "docs/tasks/catalog.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return [f"无法读取任务目录 {path}: {error}"]
    if not isinstance(payload, dict) or not isinstance(payload.get("tasks"), list):
        return ["catalog.json 顶层必须是对象且包含 tasks 数组"]
    tasks = payload["tasks"]
    ids = [task.get("id") for task in tasks if isinstance(task, dict)]
    if len(tasks) != 68:
        problems.append(f"任务总数应为 68，实际 {len(tasks)}")
    duplicates = sorted({task_id for task_id in ids if ids.count(task_id) > 1})
    if duplicates:
        problems.append(f"任务编号重复：{', '.join(duplicates)}")
    expected = expected_task_ids()
    actual = {task_id for task_id in ids if isinstance(task_id, str)}
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        problems.append(f"缺少任务：{', '.join(missing)}")
    if extra:
        problems.append(f"未知任务：{', '.join(extra)}")
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            problems.append(f"tasks[{index}] 必须是对象")
            continue
        task_id = task.get("id", f"tasks[{index}]")
        absent = sorted(REQUIRED_TASK_FIELDS - task.keys())
        if absent:
            problems.append(f"{task_id}: 缺少字段 {', '.join(absent)}")
            continue
        match = re.fullmatch(r"([A-D])-([BE])\d{2}", str(task_id))
        if not match:
            continue
        member, kind_letter = match.groups()
        expected_kind = "base" if kind_letter == "B" else "extension"
        if task["member"] != member:
            problems.append(f"{task_id}: member 应为 {member}")
        if task["kind"] != expected_kind:
            problems.append(f"{task_id}: kind 应为 {expected_kind}")
        if not re.search(r"(?:G-P|C-S|D-S)\d+", str(task["course_basis"])):
            problems.append(f"{task_id}: course_basis 缺少课程页码引用")
        if not isinstance(task["authorized_paths"], list) or not task["authorized_paths"]:
            problems.append(f"{task_id}: authorized_paths 必须是非空数组")
        if not isinstance(task["deliverables"], list) or not task["deliverables"]:
            problems.append(f"{task_id}: deliverables 必须是非空数组")
        card_relative = task["card"]
        if not isinstance(card_relative, str):
            problems.append(f"{task_id}: card 必须是相对路径字符串")
            continue
        card_path = (repo_root / card_relative).resolve()
        try:
            card_path.relative_to(repo_root.resolve())
        except ValueError:
            problems.append(f"{task_id}: card 路径越出仓库")
            continue
        try:
            card_text = card_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            problems.append(f"{task_id}: 无法读取卡片 {card_relative}: {error}")
            continue
        begin = f"AI_TASK_BEGIN {task_id}"
        end = f"AI_TASK_END {task_id}"
        meta = f"TASK_META member={member} | id={task_id} | kind={expected_kind} | schema=1"
        if card_text.count(begin) != 1 or card_text.count(end) != 1:
            problems.append(f"{task_id}: 开始/结束标记必须各出现一次")
        if card_text.count(meta) != 1:
            problems.append(f"{task_id}: TASK_META 缺失或不一致")
        lines = card_text.splitlines()
        positions = [_field_line_index(lines, field) for field in CARD_FIELDS]
        missing_fields = [field for field, position in zip(CARD_FIELDS, positions, strict=True) if position is None]
        if missing_fields:
            problems.append(f"{task_id}: 卡片缺少固定字段 {', '.join(missing_fields)}")
        elif positions != sorted(positions):
            problems.append(f"{task_id}: 卡片字段顺序不符合 schema=1")
    counts = payload.get("counts")
    if isinstance(counts, dict):
        observed_base = sum(task.get("kind") == "base" for task in tasks if isinstance(task, dict))
        observed_extension = sum(
            task.get("kind") == "extension" for task in tasks if isinstance(task, dict)
        )
        expected_values = {"total": 68, "base": 24, "extension": 44}
        observed_values = {
            "total": len(tasks),
            "base": observed_base,
            "extension": observed_extension,
        }
        for key, expected_value in expected_values.items():
            if key in counts and counts[key] != observed_values[key]:
                problems.append(
                    f"counts.{key}={counts[key]} 与实际 {observed_values[key]} 不一致"
                )
    groups_path = repo_root / "docs/integration/feature-groups.json"
    if catalog_path is None and groups_path.is_file():
        try:
            groups_payload = json.loads(groups_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            problems.append(f"无法读取功能组矩阵：{error}")
        else:
            groups = groups_payload.get("groups") if isinstance(groups_payload, dict) else None
            if not isinstance(groups, list):
                problems.append("feature-groups.json 必须包含 groups 数组")
            else:
                group_by_task: dict[str, list[str]] = {}
                known_groups: set[str] = set()
                for group in groups:
                    if not isinstance(group, dict):
                        problems.append("功能组条目必须是对象")
                        continue
                    group_id = str(group.get("id", ""))
                    if not group_id:
                        problems.append("功能组缺少 id")
                        continue
                    if group_id in known_groups:
                        problems.append(f"功能组 id 重复：{group_id}")
                    known_groups.add(group_id)
                    required = group.get("required_tasks")
                    if not isinstance(required, list) or not required:
                        problems.append(f"功能组 {group_id} 的 required_tasks 必须是非空数组")
                        continue
                    for required_task in required:
                        task_id = str(required_task)
                        group_by_task.setdefault(task_id, []).append(group_id)
                        if task_id not in extension_tasks_from_catalog(tasks):
                            problems.append(f"功能组 {group_id} 引用了未知或非扩展任务 {task_id}")
                expected_extensions = extension_tasks_from_catalog(tasks)
                uncovered = sorted(expected_extensions - group_by_task.keys())
                repeated = sorted(task for task, owners in group_by_task.items() if len(owners) > 1)
                if uncovered:
                    problems.append(f"功能组未覆盖扩展任务：{', '.join(uncovered)}")
                if repeated:
                    problems.append(f"扩展任务被多个功能组重复引用：{', '.join(repeated)}")
                for task in tasks:
                    if not isinstance(task, dict) or task.get("kind") != "extension":
                        continue
                    task_id = str(task.get("id"))
                    owners = group_by_task.get(task_id, [])
                    if len(owners) == 1 and task.get("function_group") != owners[0]:
                        problems.append(
                            f"{task_id}: function_group={task.get('function_group')}，矩阵为 {owners[0]}"
                        )
    return problems


def extension_tasks_from_catalog(tasks: list[object]) -> set[str]:
    return {
        str(task["id"])
        for task in tasks
        if isinstance(task, dict) and task.get("kind") == "extension" and isinstance(task.get("id"), str)
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--catalog", type=Path)
    args = parser.parse_args(argv)
    problems = validate_catalog(args.repo_root.resolve(), args.catalog)
    if problems:
        print("任务目录检查失败：")
        print("\n".join(f"- {problem}" for problem in problems))
        return 1
    print("任务目录检查通过：68 项任务、24 项基础、44 项扩展")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
