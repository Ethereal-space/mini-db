from __future__ import annotations

import ast
import hashlib
import json
import tomllib
from pathlib import Path

from tools.check_import_boundaries import check_import_boundaries
from tools.check_task_catalog import validate_catalog
from tools.check_utf8 import check_utf8
from tools.generate_contract_hash import compute_contract_hash, read_recorded_hash


ROOT = Path(__file__).resolve().parents[1]


def test_expected_package_skeleton_exists() -> None:
    for package in ("contracts", "frontend", "compiler", "storage", "runtime", "integration"):
        assert (ROOT / "minidb" / package / "__init__.py").is_file()

    expected_modules = {
        "frontend": ("lexer.py", "parser.py", "formatter.py"),
        "compiler": (
            "catalog_service.py",
            "semantic.py",
            "planner.py",
            "optimizer.py",
            "plan_formatter.py",
        ),
        "storage": (
            "constants.py",
            "superblock.py",
            "disk_manager.py",
            "page.py",
            "row_codec.py",
            "replacer.py",
            "buffer_pool.py",
            "table_heap.py",
            "catalog_repository.py",
        ),
        "runtime": (
            "expression_evaluator.py",
            "operators.py",
            "command_executors.py",
            "execution_service.py",
            "result_formatter.py",
            "cli_service.py",
        ),
        "integration": ("app.py", "adapters.py"),
    }
    for package, modules in expected_modules.items():
        for module in modules:
            assert (ROOT / "minidb" / package / module).is_file()
    assert (ROOT / "minidb" / "__main__.py").is_file()


def test_project_locks_python_and_has_no_runtime_dependencies() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["requires-python"] == ">=3.14,<3.15"
    assert project["dependencies"] == []
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.14.2"
    requirements = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()
    assert "pytest==9.1.1" in requirements
    assert "python-docx==1.2.0" in requirements


def test_frozen_contract_hash_matches_tree() -> None:
    assert read_recorded_hash(ROOT / "contracts.sha256") == compute_contract_hash(ROOT)


def test_task_catalog_and_cards_match() -> None:
    assert validate_catalog(ROOT) == []


def test_manual_manifest_matches_release_artifact() -> None:
    manifest_path = ROOT / "docs" / "manual" / "manual-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    document = manifest["document"]
    manual_path = ROOT / document["repository_path"]
    assert manual_path.is_file()
    assert hashlib.sha256(manual_path.read_bytes()).hexdigest() == document["sha256"]
    assert document["page_count"] == document["rendered_pages"] == 120
    assert document["page_range_pass"] is True

    structure = manifest["document_structure"]
    assert structure["task_begin_exact_count"] == 68
    assert structure["task_meta_exact_count"] == 68
    assert structure["task_end_exact_count"] == 68
    assert structure["fixed_fields_per_task"] == 16
    assert structure["fixed_field_order_pass"] is True
    assert structure["xml_replacement_character_count"] == 0
    assert manifest["visual_review"]["status"] == "PASS"
    assert manifest["visual_review"]["pages_checked"] == 120


def test_original_course_files_are_not_committed() -> None:
    forbidden_suffixes = {".pdf", ".ppt", ".pptx"}
    assert [path for path in ROOT.rglob("*") if path.is_file() and path.suffix.casefold() in forbidden_suffixes] == []


def test_all_repository_text_is_strict_utf8() -> None:
    assert check_utf8(ROOT) == []


def test_member_import_boundaries_hold() -> None:
    assert check_import_boundaries(ROOT) == []


def test_production_code_has_no_unimplemented_statements() -> None:
    problems: list[str] = []
    for path in (ROOT / "minidb").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT).as_posix()
        lowered = source.casefold()
        if "todo" in lowered or "dummy row" in lowered:
            problems.append(f"{relative}: placeholder text")
        tree = ast.parse(source)
        protocol_classes = {
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and any(isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases)
        }
        allowed_nodes = {
            child
            for protocol in protocol_classes
            for child in ast.walk(protocol)
            if isinstance(child, (ast.Pass, ast.Expr))
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Pass) and node not in allowed_nodes:
                problems.append(f"{relative}:{node.lineno}: pass")
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and node.value.value is Ellipsis
                and node not in allowed_nodes
            ):
                problems.append(f"{relative}:{node.lineno}: ellipsis")
    assert problems == []
