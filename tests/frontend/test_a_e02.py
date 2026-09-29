import json
from pathlib import Path

import pytest

from minidb.contracts.ast import SelectStmt
from minidb.contracts.errors import SyntaxError
from minidb.contracts.extensions import ExtensionStatement
from minidb.frontend import Frontend
from minidb.frontend.formatter import ast_to_data
from .helpers import position_at


def parse_order(source):
    statements = Frontend(enabled_extensions={"order_limit"}).parse(source)
    assert len(statements) == 1
    assert isinstance(statements[0], ExtensionStatement)
    return statements[0]


def test_a_e02_order_limit():
    source = "SELECT id FROM t ORDER BY age DESC,id LIMIT 2;"
    result = parse_order(source)
    assert result.feature == "order_limit" and result.version == 1
    assert set(result.payload) == {"query", "order_by", "limit"}
    assert [(term["column"]["fields"]["name"], term["direction"]) for term in result.payload["order_by"]] == [("age", "DESC"), ("id", "ASC")]
    assert result.payload["limit"] == 2
    assert result.payload["query"]["kind"] == "SelectStmt"
    assert [c["fields"]["name"] for c in result.payload["query"]["fields"]["columns"]] == ["id"]
    assert json.loads(json.dumps(result.payload)) == result.payload
    assert result.span.end.offset == len(source) - 1
    assert result.payload["query"]["span"]["end"]["offset"] == len("SELECT id FROM t")


def test_a_e02_limit_zero():
    payload = parse_order("SELECT * FROM t LIMIT 0;").payload
    assert payload["order_by"] == []
    assert payload["limit"] == 0
    assert payload["query"]["fields"]["columns"] is None


@pytest.mark.parametrize("limit,unexpected", [("-1", "-"), ("1.5", "1.5"), ("+1", "+"), ("'2'", "'2'"), ("", ""), ("id", "id")])
def test_a_e02_bad_limit(limit, unexpected):
    source = "SELECT * FROM t LIMIT " + limit
    with pytest.raises(SyntaxError) as caught:
        parse_order(source)
    assert caught.value.code == "INVALID_LIMIT"
    assert "非负整数" in caught.value.message
    assert caught.value.context["lexeme"] == unexpected
    assert caught.value.context["expected"] == ("INTEGER",)
    assert caught.value.span.start == position_at(source, len("SELECT * FROM t LIMIT "))


@pytest.mark.parametrize("suffix,unexpected", [
    ("LIMIT 2 ORDER BY id", "ORDER"), ("LIMIT 2 LIMIT 3", "LIMIT"),
    ("ORDER BY id ORDER BY id", "ORDER"), ("ORDER BY id LIMIT 2 ORDER BY id", "ORDER"),
])
def test_a_e02_clause_order(suffix, unexpected):
    source = "SELECT id FROM t " + suffix + ";"
    with pytest.raises(SyntaxError) as caught:
        parse_order(source)
    assert caught.value.code == "INVALID_CLAUSE_ORDER"
    assert caught.value.context["lexeme"] == unexpected
    assert caught.value.span.start == position_at(source, source.rindex(unexpected))


def test_a_e02_default_and_explicit_asc():
    implicit = parse_order("SELECT * FROM t ORDER BY id").payload
    explicit = parse_order("SELECT * FROM t ORDER BY id ASC").payload
    assert implicit == explicit  # 后缀外层Span不同；排序列及核心query的Span保持一致。
    assert implicit["limit"] is None


def test_a_e02_where_and_mixed_case_spans():
    source = "select NAME from T where age>=18\r\norder /*x*/ by Age dEsC,NAME aSc limit 10"
    result = parse_order(source)
    assert result.payload["query"]["fields"]["where"]["fields"]["op"] == ">="
    terms = result.payload["order_by"]
    assert [term["direction"] for term in terms] == ["DESC", "ASC"]
    assert [term["column"]["fields"]["name"] for term in terms] == ["age", "name"]
    span = terms[0]["column"]["span"]
    assert span["start"] == {"offset": source.index("Age"), "line": 2, "column": 16}


@pytest.mark.parametrize("suffix,unexpected", [
    ("ORDER id", "id"), ("ORDER BY", ""), ("ORDER BY id,", ""),
    ("ORDER BY *", "*"), ("ORDER BY id DESC ASC", "ASC"),
    ("ORDER BY id age", "age"), ("ORDER BY 1", "1"),
    ("ORDER BY id WHERE id=1", "WHERE"), ("LIMIT 2+1", "+"),
])
def test_a_e02_reject_incomplete_or_extra_suffix(suffix, unexpected):
    with pytest.raises(SyntaxError) as caught:
        parse_order("SELECT id FROM t " + suffix)
    assert caught.value.context["lexeme"] == unexpected


@pytest.mark.parametrize("suffix", ["ORDER BY id", "LIMIT 2"])
def test_a_e02_disabled(suffix):
    with pytest.raises(SyntaxError) as caught:
        Frontend().parse("SELECT * FROM t " + suffix)
    assert caught.value.code == "EXTENSION_DISABLED"
    assert "order_limit" in caught.value.message
    source = "SELECT id FROM t WHERE id=1;"
    result = Frontend(enabled_extensions={"order_limit"}).parse(source)
    assert isinstance(result[0], SelectStmt)
    assert result == Frontend().parse(source)


def test_a_e02_unknown_and_duplicate_order_columns_preserved():
    payload = parse_order("SELECT name FROM missing ORDER BY unknown DESC,unknown LIMIT 2147483648").payload
    assert [term["column"]["fields"]["name"] for term in payload["order_by"]] == ["unknown", "unknown"]
    assert payload["limit"] == 2147483648  # 手册只要求非负整数，未规定INT32上限。


def test_a_e02_snapshot_examples():
    root = Path(__file__).resolve().parents[2]
    snapshot = json.loads((root / "docs/extensions/frontend_order_limit_v1.json").read_text(encoding="utf-8"))
    for case in snapshot["valid_examples"]:
        assert ast_to_data(parse_order(case["sql"])) == case["expected"]
    assert len(snapshot["invalid_examples"]) >= 2
    for case in snapshot["invalid_examples"]:
        with pytest.raises(SyntaxError) as caught:
            parse_order(case["sql"])
        assert caught.value.context["lexeme"] == case["unexpected"]
