import json
import pytest

from minidb.contracts.ast import CreateTableStmt, InsertStmt, SelectStmt
from minidb.contracts.errors import LexicalError, SyntaxError
from minidb.contracts.ports import FrontendPort
from minidb.frontend import Frontend, Lexer
from minidb.frontend.formatter import ast_to_data, format_ast, format_tokens
from .helpers import parse_one, position_at


def test_a_b06_multiple_statements():
    source = ";CREATE TABLE t(id INT);;INSERT INTO t VALUES(1);SELECT * FROM t"
    statements = Frontend().parse(source)
    assert [type(s) for s in statements] == [CreateTableStmt, InsertStmt, SelectStmt]
    assert isinstance(Frontend(), FrontendPort)


@pytest.mark.parametrize("source", ["", " \r\n\t", ";;;", "-- only\n/* comment */ ; ;"])
def test_a_b06_comment_only(source):
    assert Frontend().parse(source) == []


def test_a_b06_missing_statement_separator():
    source = "SELECT * FROM t SELECT * FROM t;"
    with pytest.raises(SyntaxError) as caught:
        Frontend().parse(source)
    assert caught.value.span.start == position_at(source, source.index("SELECT", 1))
    assert caught.value.span.start.column == 17
    assert set(caught.value.context["expected"]) == {"SEMICOLON", "EOF"}


def test_a_b06_stable_formatter():
    source = "SELECT id FROM t WHERE id=1;"
    first, second = Frontend().parse(source), Frontend().parse(source)
    assert format_ast(first) == format_ast(second)
    data = json.loads(format_ast(first))
    assert data == ast_to_data(first)
    assert data[0]["kind"] == "SelectStmt"
    where = data[0]["fields"]["where"]
    assert where["kind"] == "BinaryExpr"
    assert where["fields"]["left"]["fields"]["name"] == "id"
    assert where["fields"]["right"]["fields"]["value"] == 1
    assert where["span"]["start"]["offset"] == source.rindex("id")
    assert "object at 0x" not in format_ast(first)


def test_a_b06_formatter_snapshot():
    token_text = format_tokens(Lexer("'张\n三'").tokenize())
    assert json.loads(token_text)[0] == {
        "kind": "STRING", "lexeme": "'张\n三'", "value": "张\n三",
        "span": {"start": {"offset": 0, "line": 1, "column": 1},
                 "end": {"offset": 5, "line": 2, "column": 3}},
    }
    assert "\\n" in token_text
    assert "张" in token_text
    expected = {"kind": "Identifier", "fields": {"name": "id"}, "span": {
        "start": {"offset": 7, "line": 1, "column": 8},
        "end": {"offset": 9, "line": 1, "column": 10}}}
    assert ast_to_data(parse_one("SELECT id FROM t").columns[0]) == expected


def test_a_b06_error_and_trace():
    events = []
    errors = []
    for frontend in (Frontend(events.append), Frontend()):
        with pytest.raises(SyntaxError) as caught:
            frontend.parse("SELECT id FROM;")
        errors.append(caught.value)
    assert errors[0].span == errors[1].span
    assert errors[0].context == errors[1].context
    assert errors[0].span.start.column == 15
    assert errors[0].context["expected"] == ("IDENTIFIER",)
    assert [e.stage for e in events] == ["TOKEN"]


def test_a_b06_trace_once_per_batch_and_no_shared_state():
    events = []
    frontend = Frontend(events.append)
    frontend.parse("SELECT * FROM t;SELECT id FROM t;DELETE FROM t")
    assert [event.stage for event in events] == ["TOKEN", "AST"]
    assert len(json.loads(events[1].detail)) == 3
    events.clear()
    assert frontend.parse("") == []
    assert [event.stage for event in events] == ["TOKEN", "AST"]
    assert json.loads(events[1].detail) == []


def test_a_b06_failure_never_reports_partial_ast():
    events = []
    frontend = Frontend(events.append)
    with pytest.raises(SyntaxError):
        frontend.parse("CREATE TABLE t(id INT); SELECT FROM t;")
    assert [event.stage for event in events] == ["TOKEN"]
    assert frontend.parse("SELECT * FROM t")[0].table.name == "t"


def test_a_b06_lexical_error_no_success_events():
    events = []
    with pytest.raises(LexicalError):
        Frontend(events.append).parse("SELECT * FROM t; @")
    assert events == []


def test_a_b06_eof_location_and_semicolon_in_string():
    source = "SELECT id FROM\r\n"
    with pytest.raises(SyntaxError) as caught:
        Frontend().parse(source)
    assert caught.value.span.start == caught.value.span.end == position_at(source, len(source))
    statements = Frontend().parse("INSERT INTO t VALUES('x;y'); -- ;\n SELECT * FROM t")
    assert len(statements) == 2
    assert statements[0].values[0].value == "x;y"


def test_a_b06_callback_programming_error_is_not_swallowed():
    def callback(event):
        raise RuntimeError("observer bug")
    with pytest.raises(RuntimeError, match="observer bug"):
        Frontend(callback).parse("SELECT * FROM t")


@pytest.mark.parametrize("mode", ["and", "not"])
def test_a_b06_long_expression_and_trace_without_recursion_error(mode):
    expression = " AND ".join(["id=1"] * 1500) if mode == "and" else "NOT " * 1500 + "id=1"
    events = []
    statements = Frontend(events.append).parse("SELECT * FROM t WHERE " + expression)
    node = statements[0].where
    count = 0
    while getattr(node, "op", None) == ("AND" if mode == "and" else "NOT"):
        count += 1
        node = node.left if mode == "and" else node.operand
    assert count == (1499 if mode == "and" else 1500)
    assert node.op == "="
    assert [e.stage for e in events] == ["TOKEN", "AST"]
    assert '"SelectStmt"' in events[-1].detail


def test_a_b06_huge_integer_formatter():
    digits = "1" + "0" * 5000
    statements = Frontend().parse("INSERT INTO t VALUES(" + digits + ")")
    assert '"value": ' + digits in format_ast(statements)
    assert '"value": ' + digits in format_tokens(Lexer(digits).tokenize())
