"""只读预检四份成员交付，输出核心完整性与扩展选择 JSON。"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

if __package__:
    from .generate_contract_hash import compute_contract_hash, read_recorded_hash
else:
    from generate_contract_hash import compute_contract_hash, read_recorded_hash


MEMBERS = ("A", "B", "C", "D")
PASS_STATUSES = frozenset({"pass", "passed", "ok", "success", "成功", "通过"})


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取 JSON {path}: {error}") from error


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def read_declared_tasks(cli_values: Iterable[str], tasks_file: Path | None) -> set[str]:
    declared: set[str] = set()
    for value in cli_values:
        declared.update(_string_list(value))
    if tasks_file is None:
        return declared
    payload = _read_json(tasks_file)
    if isinstance(payload, list) or isinstance(payload, str):
        declared.update(_string_list(payload))
        return declared
    if not isinstance(payload, dict):
        raise ValueError("任务清单必须是数组、逗号分隔字符串或 JSON 对象")
    for key in ("declared_tasks", "completed_extensions", "extensions", "tasks"):
        if key in payload:
            declared.update(_string_list(payload[key]))
            return declared
    raise ValueError("任务清单对象缺少 declared_tasks 或 completed_extensions 字段")


def _manifest_candidates(base: Path, member: str, input_mode: bool) -> list[Path]:
    lower = member.casefold()
    if input_mode:
        copy_root = base / f"member_{lower}"
        return [
            copy_root / "delivery" / f"member_{lower}.json",
            copy_root / "manifest.json",
            copy_root / f"member_{lower}.json",
        ]
    return [base / f"member_{lower}.json"]


def _evidenced_tasks(manifest: Mapping[str, object]) -> set[str]:
    evidence = set(_string_list(manifest.get("verified_tasks")))
    raw_results = manifest.get("test_results", manifest.get("tests", []))
    if not isinstance(raw_results, list):
        return evidence
    for result in raw_results:
        if not isinstance(result, dict):
            continue
        status = str(result.get("status", "")).casefold()
        if status not in PASS_STATUSES:
            continue
        evidence.update(_string_list(result.get("tasks", result.get("task_ids"))))
        evidence.update(_string_list(result.get("task_id")))
    return evidence


def load_manifests(
    source: Path,
    *,
    input_mode: bool,
    expected_contract_hash: str,
) -> tuple[dict[str, dict[str, object]], list[dict[str, str]], list[str]]:
    manifests: dict[str, dict[str, object]] = {}
    hash_mismatches: list[dict[str, str]] = []
    errors: list[str] = []
    for member in MEMBERS:
        candidates = _manifest_candidates(source, member, input_mode)
        manifest_path = next((path for path in candidates if path.is_file()), None)
        if manifest_path is None:
            errors.append(f"成员 {member} 缺少交付清单")
            continue
        try:
            raw = _read_json(manifest_path)
        except ValueError as error:
            errors.append(str(error))
            continue
        if not isinstance(raw, dict):
            errors.append(f"{manifest_path}: 交付清单顶层必须是对象")
            continue
        manifest = dict(raw)
        declared_member = str(manifest.get("member", "")).upper()
        if declared_member != member:
            errors.append(f"{manifest_path}: member 应为 {member}，实际 {declared_member or '缺失'}")
        manifests[member] = manifest
        recorded_hash = str(manifest.get("contract_hash", "")).casefold()
        actual_hash = recorded_hash
        if input_mode:
            copy_root = source / f"member_{member.casefold()}"
            try:
                actual_hash = compute_contract_hash(copy_root)
            except OSError as error:
                errors.append(f"成员 {member} 无法计算契约摘要：{error}")
                actual_hash = ""
            if recorded_hash and actual_hash and recorded_hash != actual_hash:
                hash_mismatches.append(
                    {
                        "member": member,
                        "expected": actual_hash,
                        "actual": recorded_hash,
                        "source": "delivery_manifest",
                    }
                )
        if actual_hash != expected_contract_hash:
            hash_mismatches.append(
                {
                    "member": member,
                    "expected": expected_contract_hash,
                    "actual": actual_hash or "missing",
                    "source": "contract_tree" if input_mode else "delivery_manifest",
                }
            )
    return manifests, hash_mismatches, errors


def _group_mode(group: Mapping[str, object]) -> str:
    mode = str(group.get("mode", "all_required")).casefold().replace("-", "_")
    category = str(group.get("category", "")).casefold().replace("-", "_")
    if mode == "retain_only" or category in {"retain_only", "incomplete_chain"}:
        return "retain_only"
    if mode == "experimental" or category == "experimental":
        return "experimental"
    if mode == "per_task":
        return "per_task"
    return "all_required"


def build_preflight_report(
    *,
    catalog: Mapping[str, object],
    feature_groups: Mapping[str, object],
    manifests: Mapping[str, Mapping[str, object]],
    declared_tasks: set[str],
    expected_contract_hash: str,
    contract_mismatches: list[dict[str, str]] | None = None,
    manifest_errors: list[str] | None = None,
) -> dict[str, object]:
    task_entries = catalog.get("tasks")
    groups = feature_groups.get("groups")
    if not isinstance(task_entries, list):
        raise ValueError("catalog.tasks 必须是数组")
    if not isinstance(groups, list):
        raise ValueError("feature-groups.groups 必须是数组")
    task_by_id = {
        str(task["id"]): task
        for task in task_entries
        if isinstance(task, dict) and isinstance(task.get("id"), str)
    }
    base_tasks = sorted(
        task_id for task_id, task in task_by_id.items() if task.get("kind") == "base"
    )
    extension_tasks = {
        task_id for task_id, task in task_by_id.items() if task.get("kind") == "extension"
    }
    if len(base_tasks) != 24:
        raise ValueError(f"任务目录必须含 24 项基础任务，实际 {len(base_tasks)}")

    completed: set[str] = set()
    evidenced: set[str] = set()
    invalid_claims: list[str] = []
    for member, manifest in manifests.items():
        member_tasks = set(_string_list(manifest.get("completed_tasks")))
        for task_id in member_tasks:
            if not task_id.startswith(f"{member}-"):
                invalid_claims.append(f"成员 {member} 声明了其他成员任务 {task_id}")
                continue
            completed.add(task_id)
        evidenced.update(_evidenced_tasks(manifest) & member_tasks)

    unknown_declared = sorted(declared_tasks - extension_tasks)
    requested = declared_tasks & extension_tasks
    missing_base = sorted(set(base_tasks) - completed)
    missing_test_evidence = sorted(set(base_tasks) & completed - evidenced)
    unknown_completed = sorted(completed - set(task_by_id))
    mismatches = list(contract_mismatches or [])
    errors = list(manifest_errors or []) + invalid_claims
    blocked_by_contract = bool(mismatches)

    enabled: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    retained_only: list[dict[str, object]] = []
    experimental_ready: list[dict[str, object]] = []
    covered_requested: set[str] = set()

    for raw_group in groups:
        if not isinstance(raw_group, dict):
            raise ValueError("feature-groups.groups 的每项必须是对象")
        group_id = str(raw_group.get("id", raw_group.get("name", "unnamed")))
        required = set(_string_list(raw_group.get("required_tasks")))
        requested_here = requested & required
        if not requested_here:
            continue
        covered_requested.update(requested_here)
        mode = _group_mode(raw_group)
        if mode in {"per_task", "experimental"}:
            available = sorted(requested_here & completed & evidenced)
            missing = sorted(requested_here - completed)
            unverified = sorted(requested_here & completed - evidenced)
            if mode == "experimental":
                if available:
                    experimental_ready.append({"group": group_id, "tasks": available})
            elif available and not blocked_by_contract:
                enabled.append({"group": group_id, "tasks": available})
            elif available:
                skipped.append(
                    {"group": group_id, "tasks": available, "missing_tasks": [], "reason": "contract_mismatch"}
                )
            if missing:
                skipped.append(
                    {"group": group_id, "tasks": sorted(requested_here), "missing_tasks": missing, "reason": "implementation_missing"}
                )
            if unverified:
                skipped.append(
                    {"group": group_id, "tasks": sorted(requested_here), "missing_tasks": unverified, "reason": "test_evidence_missing"}
                )
            continue

        missing_declared = required - requested
        missing_implementation = required - completed
        missing_evidence = required - evidenced
        missing = sorted(missing_declared | missing_implementation | missing_evidence)
        entry = {"group": group_id, "tasks": sorted(requested_here), "missing_tasks": missing}
        if mode == "retain_only":
            if missing:
                skipped.append({**entry, "reason": "incomplete_retain_only_group"})
            elif blocked_by_contract:
                skipped.append({**entry, "reason": "contract_mismatch"})
            else:
                retained_only.append({"group": group_id, "tasks": sorted(required)})
            continue
        if missing:
            skipped.append({**entry, "reason": "incomplete_atomic_group"})
        elif blocked_by_contract:
            skipped.append({**entry, "reason": "contract_mismatch"})
        else:
            enabled.append({"group": group_id, "tasks": sorted(required)})

    for task_id in sorted(requested - covered_requested):
        skipped.append(
            {"group": None, "tasks": [task_id], "missing_tasks": [], "reason": "no_feature_group"}
        )

    ignored = sorted((completed & extension_tasks) - requested)
    missing_extensions = sorted(requested - completed)
    missing_extension_evidence = sorted(requested & completed - evidenced)
    core_ready = not (
        missing_base
        or missing_test_evidence
        or mismatches
        or errors
        or unknown_declared
        or unknown_completed
    )
    if not core_ready:
        status = "BLOCKED_CORE"
    elif skipped:
        status = "READY_WITH_SKIPPED_EXTENSIONS"
    else:
        status = "READY"
    return {
        "schema_version": 1,
        "status": status,
        "ready_for_core": core_ready,
        "baseline_contract_hash": expected_contract_hash,
        "declared_extensions": sorted(requested),
        "completed_tasks": sorted(completed & set(task_by_id)),
        "missing": {
            "base_tasks": missing_base,
            "base_test_evidence": missing_test_evidence,
            "declared_extensions": missing_extensions,
            "extension_test_evidence": missing_extension_evidence,
        },
        "contract_mismatches": mismatches,
        "manifest_errors": errors,
        "unknown_declared_tasks": unknown_declared,
        "unknown_completed_tasks": unknown_completed,
        "enabled": enabled,
        "skipped": skipped,
        "retained_only": retained_only,
        "experimental_ready": experimental_ready,
        "ignored_unlisted_extensions": ignored,
    }


def preflight(
    *,
    repo_root: Path,
    declared_tasks: set[str],
    catalog_path: Path | None = None,
    groups_path: Path | None = None,
    delivery_dir: Path | None = None,
    input_root: Path | None = None,
) -> dict[str, object]:
    root = repo_root.resolve()
    catalog = _read_json(catalog_path or root / "docs/tasks/catalog.json")
    groups = _read_json(groups_path or root / "docs/integration/feature-groups.json")
    if not isinstance(catalog, dict) or not isinstance(groups, dict):
        raise ValueError("目录与功能组文件的顶层都必须是 JSON 对象")
    expected_hash = read_recorded_hash(root / "contracts.sha256")
    source = input_root.resolve() if input_root else (delivery_dir or root / "delivery").resolve()
    manifests, mismatches, errors = load_manifests(
        source,
        input_mode=input_root is not None,
        expected_contract_hash=expected_hash,
    )
    return build_preflight_report(
        catalog=catalog,
        feature_groups=groups,
        manifests=manifests,
        declared_tasks=declared_tasks,
        expected_contract_hash=expected_hash,
        contract_mismatches=mismatches,
        manifest_errors=errors,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--task",
        action="append",
        default=[],
        help="用户声明完成的扩展编号；可重复或用逗号分隔",
    )
    parser.add_argument("--tasks-file", type=Path, help="JSON 格式的用户任务清单")
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--feature-groups", type=Path)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--delivery-dir", type=Path, help="直接读取四份 delivery JSON")
    source.add_argument("--input-root", type=Path, help="读取 member_a 至 member_d 四份项目副本")
    parser.add_argument("--output", type=Path, help="另外把报告写入 JSON 文件")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        declared = read_declared_tasks(args.task, args.tasks_file)
        report = preflight(
            repo_root=args.repo_root,
            declared_tasks=declared,
            catalog_path=args.catalog,
            groups_path=args.feature_groups,
            delivery_dir=args.delivery_dir,
            input_root=args.input_root,
        )
    except (OSError, ValueError) as error:
        report = {"schema_version": 1, "status": "INVALID_INPUT", "ready_for_core": False, "error": str(error)}
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    print(rendered, end="")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    return 0 if report.get("ready_for_core") else 1


if __name__ == "__main__":
    raise SystemExit(main())
