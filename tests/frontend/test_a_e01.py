from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path

import pytest

from minidb.contracts.ast import Identifier, SelectStmt
from minidb.contracts.errors import MiniDBError, SyntaxError
from minidb.contracts.extensions import ExtensionStatement
from minidb.contracts.source import Position, Span
from minidb.frontend import Frontend, Lexer, Parser
from minidb.frontend.formatter import ast_to_data, format_ast
from tools.generate_contract_hash import compute_contract_hash, read_recorded_hash
from .helpers import position_at


def parse_update(source):
    statements = Frontend(enabled_extensions={"update"}).parse(source)
    assert len(statements) == 1
    assert isinstance(statements[0], ExtensionStatement)
    return statements[0]


def test_a_e01_update_payload():
    source = "UPDATE student SET age=21,name='李四' WHERE id=1;"
    result = parse_update(source)
    assert result.feature == "update" and result.version == 1
    payload = result.payload
    assert set(payload) == {"table", "assignments", "where"}
    assert payload["table"]["fields"]["name"] == "student"
    assert [a["column"]["fields"]["name"] for a in payload["assignments"]] == ["age", "name"]
    assert [a["expr"]["fields"]["value"] for a in payload["assignments"]] == [21, "李四"]
    assert payload["where"]["kind"] == "BinaryExpr"
    assert payload["where"]["fields"]["op"] == "="
    assert payload["where"]["fields"]["left"]["fields"]["name"] == "id"
    assert payload["where"]["fields"]["right"]["fields"]["value"] == 1
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload
    assert source[result.span.start.offset:result.span.end.offset] == source[:-1]


def test_a_e01_update_all():
    payload = parse_update("UPDATE t SET id=2;").payload
    assert payload["where"] is None
    assert len(payload["assignments"]) == 1
    assert payload["assignments"][0]["expr"]["fields"]["value"] == 2


def test_a_e01_missing_value():
    source = "UPDATE t SET id=;"
    with pytest.raises(SyntaxError) as caught:
        parse_update(source)
    assert caught.value.context["actual"] == "SEMICOLON"
    assert "INTEGER" in caught.value.context["expected"]
    assert caught.value.span.start == position_at(source, source.index(";"))
    assert caught.value.span.end.offset == len(source)


def test_a_e01_disabled():
    with pytest.raises(SyntaxError) as caught:
        Frontend().parse("UPDATE t SET id=2;")
    assert caught.value.code == "EXTENSION_DISABLED"
    assert "update" in caught.value.message and "未启用" in caught.value.message
    source = "SELECT id FROM t WHERE id=2"
    assert Frontend().parse(source) == Frontend(enabled_extensions={"update"}).parse(source)


def test_a_e01_preserve_duplicates_and_semantic_errors():
    result = parse_update("UPDATE missing SET id='wrong',id=2147483648 WHERE unknown=1;")
    assert [a["column"]["fields"]["name"] for a in result.payload["assignments"]] == ["id", "id"]
    assert [a["expr"]["fields"]["value"] for a in result.payload["assignments"]] == ["wrong", 2147483648]
    assert result.payload["where"]["fields"]["left"]["fields"]["name"] == "unknown"


def test_a_e01_reuse_expressions_and_real_spans():
    source = "-- x\r\nUpDaTe T SET age=-2,flag=NOT age=18 OR id=1 AND id=2\r\nWHERE name='Tom''s book';"
    result = parse_update(source)
    assignments = result.payload["assignments"]
    assert result.span.start == position_at(source, source.index("UpDaTe"))
    assert assignments[0]["expr"]["fields"]["value"] == -2
    value_span = assignments[0]["expr"]["span"]
    assert source[value_span["start"]["offset"]:value_span["end"]["offset"]] == "-2"
    expr = assignments[1]["expr"]
    assert expr["fields"]["op"] == "OR"
    assert expr["fields"]["left"]["fields"]["op"] == "NOT"
    assert expr["fields"]["right"]["fields"]["op"] == "AND"
    assert result.payload["where"]["fields"]["right"]["fields"]["value"] == "Tom's book"


@pytest.mark.parametrize("source,token", [
    ("UPDATE t id=1", "id"), ("UPDATE SET id=1", "SET"),
    ("UPDATE t SET", ""), ("UPDATE t SET id", ""),
        ("UPDATE t SET id=1, WHERE id=2", "WHERE"),
    ("UPDATE t SET id=1 name='x'", "name"),
    ("UPDATE t SET id=1+2", "+"), ("UPDATE t SET id=1 WHERE", ""),
    ("UPDATE t SET id=1 ORDER BY id", "ORDER"),
    ("UPDATE t SET id=(1", ""), ("UPDATE t SET id=1,", ""),
])
def test_a_e01_reject_malformed(source, token):
    with pytest.raises(SyntaxError) as caught:
        parse_update(source)
    assert caught.value.context["lexeme"] == token
    assert caught.value.context["expected"]


def test_a_e01_trace_and_atomic_batch():
    events = []
    frontend = Frontend(events.append, enabled_extensions={"update"})
    result = frontend.parse("SELECT * FROM t;UPDATE t SET id=2")
    assert isinstance(result[0], SelectStmt)
    assert isinstance(result[1], ExtensionStatement)
    assert [e.stage for e in events] == ["TOKEN", "AST"]
    data = json.loads(events[-1].detail)
    assert data == ast_to_data(result)
    assert data[1]["fields"]["feature"] == "update"
    events.clear()
    with pytest.raises(SyntaxError):
        frontend.parse("UPDATE t SET id=1;UPDATE t SET id=;")
    assert [e.stage for e in events] == ["TOKEN"]


def test_a_e01_payload_copy_and_json_only():
    span = Span(Position(0, 1, 1), Position(1, 1, 2))
    payload = {"items": [{"x": 1}]}
    result = ExtensionStatement("update", 1, payload, span)
    payload["items"][0]["x"] = 2
    assert result.payload == {"items": [{"x": 1}]}
    with pytest.raises(FrozenInstanceError):
        result.version = 2
    for invalid in ({"node": Identifier("x", span)}, {"tuple": (1,)}, {1: "key"}):
        with pytest.raises(TypeError):
            ExtensionStatement("update", 1, invalid, span)
    # 冻结契约只接受 JSON 值；非 JSON 对象必须在构造扩展信封时被拒绝。
    with pytest.raises(TypeError):
        ExtensionStatement("update", 1, {"self": object()}, span)


def test_a_e01_explicit_flags_and_parser_entry():
    with pytest.raises(MiniDBError) as caught:
        Frontend(enabled_extensions={"unknown"})
    assert caught.value.stage == "UNSUPPORTED" and caught.value.span is None
    with pytest.raises(TypeError):
        Frontend(enabled_extensions="update")
    result = Parser(Lexer("UPDATE t SET id=2").tokenize(), enabled_extensions={"update"}).parse()
    assert result[0].feature == "update"


def test_a_e01_long_expression_json_copy_and_format():
    # 冻结 ExtensionStatement 对 JSON 结构做递归校验；使用足够深但不触及
    # Python 默认递归上限的链，验证 AST 编码仍不会依赖对象 repr。
    result = parse_update("UPDATE t SET id=1 WHERE " + " AND ".join(["id=1"] * 200))
    node = result.payload["where"]
    count = 0
    while node["fields"]["op"] == "AND":
        count += 1
        node = node["fields"]["left"]
    assert count == 199
    assert '"ExtensionStatement"' in format_ast(result)


def test_a_e01_snapshot_examples():
    root = Path(__file__).resolve().parents[2]
    snapshot = json.loads((root / "docs/extensions/frontend_update_v1.json").read_text(encoding="utf-8"))
    for case in snapshot["valid_examples"]:
        assert ast_to_data(parse_update(case["sql"])) == case["expected"]
    assert len(snapshot["invalid_examples"]) >= 2
    for case in snapshot["invalid_examples"]:
        with pytest.raises(SyntaxError) as caught:
            parse_update(case["sql"])
        assert caught.value.context["lexeme"] == case["unexpected"]


def test_a_e01_original_core_contracts_unchanged():
    root = Path(__file__).resolve().parents[2]
    assert read_recorded_hash(root / "contracts.sha256") == compute_contract_hash(root)


def test_a_e01_extension_configuration_is_per_instance():
    flags = {"update"}
    frontend = Frontend(enabled_extensions=flags)
    flags.clear()
    assert frontend.parse("UPDATE t SET id=2")[0].feature == "update"
    with pytest.raises(SyntaxError) as caught:
        Frontend().parse("UPDATE t SET id=2")
    assert caught.value.code == "EXTENSION_DISABLED"
