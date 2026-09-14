"""从真实 JUnit、源码与日志生成成员 A 清单，不修改生产代码。"""

from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def _git_commit_exists(root: Path) -> bool:
    """判断当前副本是否已有提交，不依赖系统是否能直接找到 git 命令。"""
    git_dir = root / ".git"
    head = git_dir / "HEAD"
    if not head.is_file():
        return False
    value = head.read_text(encoding="utf-8").strip()
    if not value.startswith("ref: "):
        return len(value) >= 40
    ref_name = value.removeprefix("ref: ")
    loose_ref = git_dir / ref_name
    if loose_ref.is_file() and loose_ref.read_text(encoding="utf-8").strip():
        return True
    packed_refs = git_dir / "packed-refs"
    return packed_refs.is_file() and any(
        line.strip() and not line.startswith(("#", "^")) and line.endswith(f" {ref_name}")
        for line in packed_refs.read_text(encoding="utf-8").splitlines()
    )


def record_delivery(root: Path) -> None:
    tree = ET.parse(root / "delivery/test-results.xml")
    suites = list(tree.iter("testsuite"))
    total = sum(int(s.attrib.get("tests", 0)) for s in suites)
    failed = sum(int(s.attrib.get("failures", 0)) + int(s.attrib.get("errors", 0)) for s in suites)
    skipped = sum(int(s.attrib.get("skipped", 0)) for s in suites)
    if failed or skipped:
        raise ValueError("只有无失败、无跳过的真实验收结果才能记录功能通过")
    test_names = defaultdict(list)
    for test in tree.iter("testcase"):
        test_names[test.attrib["classname"]].append(test.attrib["name"])
    symbols = {
        "A-B01": ["Lexer.advance", "Lexer.scan_identifier", "Lexer.tokenize"],
        "A-B02": ["Lexer.scan_number", "Lexer.scan_string", "Lexer.scan_operator_or_comment"],
        "A-B03": ["Parser.expect", "Parser.parse_create_table", "Parser.parse_insert", "Parser.parse_literal"],
        "A-B04": ["Parser.parse_select", "Parser.parse_delete", "Parser.parse_projection"],
        "A-B05": ["Parser.parse_or", "Parser.parse_and", "Parser.parse_not", "Parser.parse_comparison", "Parser.parse_primary"],
        "A-B06": ["Frontend.parse", "Parser.parse", "ast_to_data", "format_ast", "format_tokens"],
    }
    extension_symbols = {
        "A-E01": ["Parser.parse_update", "Parser.parse_assignment", "ExtensionStatement"],
        "A-E02": ["Parser.parse_query_suffix", "Parser.parse_order_terms", "Parser.parse_limit"],
        "A-E03": ["Parser.parse_select", "Parser._require_extension"],
    }
    extension_delivery = {
        "A-E01": {"feature": "update", "version": 1, "group": "EXT_UPDATE",
                  "execution_dependencies": ["B-E04", "C-E07", "D-E02"]},
        "A-E02": {"feature": "order_limit", "version": 1, "group": "EXT_ORDER_LIMIT",
                  "execution_dependencies": ["B-E05", "D-E03"]},
        "A-E03": {"feature": "distinct", "version": 1, "group": "EXT_DISTINCT",
                  "execution_dependencies": ["另行定义B去重计划和D去重算子；现有任务未覆盖"]},
    }
    files = sorted(p for folder in ("minidb", "tests", "demo", "tools")
                   for p in (root / folder).rglob("*.py"))
    snapshot = "".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  "
                       + p.relative_to(root).as_posix() + "\n" for p in files)
    (root / "delivery/source.sha256").write_text(snapshot, encoding="utf-8")
    created = sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                     if p.is_file() and not any(part.startswith((".git", ".idea", ".venv", "__pycache__", "pytest-cache"))
                                               for part in p.relative_to(root).parts)
                     and not p.relative_to(root).as_posix().startswith("delivery/"))
    task_symbols = symbols | extension_symbols
    task_ids = list(task_symbols)
    tasks = []
    for task_id, entries in task_symbols.items():
        stem = "test_" + task_id.lower().replace("-", "_")
        names = test_names[f"tests.frontend.{stem}"]
        if not names:
            raise ValueError(f"缺少真实任务测试：{task_id}")
        tasks.append({"id": task_id, "functional_status": "PASS",
                      "understanding_status": "PENDING_MEMBER_REVIEW",
                      "symbols": entries, "test_file": f"tests/frontend/{stem}.py",
                      "test_count": len(names), "tests": names})
    record = {
        "schema_version": 1, "member": "A", "feature_group": "CORE_FRONTEND",
        "contract_hash": (root / "contracts.sha256").read_text(encoding="utf-8").strip().split()[0],
        "generated_at": datetime.now().astimezone().isoformat(),
        "completed_tasks": task_ids,
        "verified_tasks": task_ids + ["contracts-frontend", "compileall", "diff-check"],
        "modified_files": [
            "minidb/frontend/lexer.py", "minidb/frontend/parser.py", "minidb/frontend/formatter.py",
            "minidb/frontend/__init__.py", "tests/frontend", "docs/walkthrough/member_a.md",
            "delivery/member_a.json",
        ],
        "test_results": [{"status": "passed", "tasks": task_ids,
                          "command": "python -m pytest -q tests/contracts tests/frontend"}],
        "completed_extensions": list(extension_symbols),
        "extensions": extension_delivery, "extensions_enabled_by_default": [],
        "database_execution_available": False,
        "functional_status": "PASS", "understanding_status": "PENDING_MEMBER_REVIEW",
        "baseline": {
            "status": "MERGED_FRONTEND_ADAPTED_TO_FROZEN_BASELINE",
            "reason": "成员 A 的前端实现已适配当前 main 冻结契约；统一以仓库 contracts.sha256 为准，未修改 minidb/contracts。",
            "contract_manifest": "contracts.sha256",
            "contract_manifest_sha256": hashlib.sha256((root / "contracts.sha256").read_bytes()).hexdigest(),
            "scope": ["source", "tokens", "ast", "errors", "TraceEvent", "FrontendPort", "ExtensionStatement"],
            "previous_core_manifest": "docs/extensions/contracts.core-v1.sha256",
            "previous_core_manifest_sha256": hashlib.sha256((root / "docs/extensions/contracts.core-v1.sha256").read_bytes()).hexdigest(),
            "added_contract_files": [],
        },
        "environment": {"python": "3.14.2", "pip": "25.3", "pytest": "9.1.1",
                        "platform_tested": "Windows", "runtime_dependencies": "standard library only"},
        "tests": {"passed": total, "failed": failed, "skipped": skipped,
                  "junit": "delivery/test-results.xml", "command_log": "delivery/validation.txt"},
        "tasks": tasks, "created_files": created,
        "member_owned_paths": ["minidb/frontend", "tests/frontend", "docs/extensions", "docs/walkthrough/member_a.md", "delivery/member_a.json"],
        "bootstrap_support_paths": ["minidb/contracts", "minidb/__init__.py", "tests/contracts", "tests/__init__.py",
                                    "docs/grammar.md", "docs/INTERFACES.md", "README.md", "pyproject.toml",
                                    ".python-version", ".gitignore", "demo", "tools", "contracts.sha256"],
        "walkthrough": "docs/walkthrough/member_a.md",
        "source_manifest": "delivery/source.sha256",
        "source_snapshot_sha256": hashlib.sha256(snapshot.encode("utf-8")).hexdigest(),
        "handoff": "复制此完整源码项目到最终 _integration_input/member_a，排除 .venv 和缓存；整合前统一公共契约。",
        "limits": ["只完成 SQL 前端，没有 B/C/D 或数据库执行、持久化验收",
                   "括号嵌套上限64，超过时报SYNTAX/NESTING_LIMIT",
                   "超出有限float范围的小数字面量报LEXICAL/INVALID_NUMBER",
                   "个人口述、Debug、现场修改待本人完成；未运行Linux验收"],
        "git_commit_or_push_performed": _git_commit_exists(root),
    }
    (root / "delivery/member_a.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    record_delivery(Path(__file__).resolve().parents[1])
