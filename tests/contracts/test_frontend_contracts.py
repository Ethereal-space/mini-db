from dataclasses import FrozenInstanceError, fields
from pathlib import Path
import ast
import hashlib
import pytest

from minidb.contracts.ast import Identifier, SelectStmt
from minidb.contracts.errors import LexicalError, MiniDBError, SyntaxError
from minidb.contracts.source import Position, Span
from minidb.contracts.tokens import Token
from minidb.frontend import Frontend


def test_frozen_fields_and_tuple_payloads():
    stmt = Frontend().parse("SELECT id,id FROM t WHERE id=1")[0]
    assert [f.name for f in fields(Token)] == ["kind", "lexeme", "value", "span"]
    assert [f.name for f in fields(SelectStmt)] == ["table", "columns", "where", "span"]
    assert isinstance(stmt.columns, tuple)
    assert isinstance(stmt.table, Identifier)
    with pytest.raises(FrozenInstanceError):
        stmt.table.name = "changed"
    with pytest.raises(FrozenInstanceError):
        stmt.where.op = "!="


def test_span_rejects_backwards_range():
    with pytest.raises(ValueError):
        Span(Position(2, 1, 3), Position(1, 1, 2))
    with pytest.raises(ValueError):
        Position(0, 0, 1)


def test_error_fields_and_non_sql_location():
    error = MiniDBError("IO", "READ_FAILED", "读页失败", context={"page_id": 8})
    assert error.span is None
    assert error.context == {"page_id": 8}
    assert "line" not in str(error)
    with pytest.raises(LexicalError) as caught:
        Frontend().parse("SELECT @")
    assert caught.value.stage == "LEXICAL"
    with pytest.raises(SyntaxError) as caught:
        Frontend().parse("SELECT FROM t")
    assert caught.value.stage == "SYNTAX"
    assert caught.value.context["expected"] == ("IDENTIFIER",)


def test_frontend_import_boundary():
    root = Path(__file__).resolve().parents[2]
    forbidden = {"sqlite3", "sqlalchemy", "lark", "ply", "tests"}
    for path in (root / "minidb/frontend").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                assert module.split(".")[0] not in forbidden
                if module.startswith("minidb."):
                    assert module.startswith(("minidb.contracts", "minidb.frontend")), (path, module)


def test_contract_hash_matches_frozen_snapshot():
    root = Path(__file__).resolve().parents[2]
    manifest = root / "contracts.sha256"
    expected = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        expected[name] = digest
    actual = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted((root / "minidb/contracts").glob("*.py"))}
    assert expected == actual
