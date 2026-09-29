import pytest

from minidb.contracts.errors import SyntaxError
from minidb.frontend.lexer import Lexer
from minidb.frontend.parser import Parser
from .helpers import parse_one, position_at, shape


def test_a_b05_and_or_tree():
    expr = parse_one("SELECT * FROM t WHERE a=1 OR b=2 AND c=3;").where
    assert shape(expr) == ("OR", ("=", "a", 1), ("AND", ("=", "b", 2), ("=", "c", 3)))


def test_a_b05_not_comparison():
    expr = parse_one("SELECT * FROM t WHERE NOT age=18;").where
    assert shape(expr) == ("NOT", ("=", "age", 18))


def test_a_b05_parentheses_and_alias():
    source = "SELECT * FROM t WHERE NOT NOT (a==1 OR b<>2);"
    expr = parse_one(source).where
    assert shape(expr) == ("NOT", ("NOT", ("OR", ("=", "a", 1), ("!=", "b", 2))))
    group = expr.operand.operand
    assert source[group.span.start.offset:group.span.end.offset] == "(a==1 OR b<>2)"
    assert source[group.left.left.span.start.offset:group.left.left.span.end.offset] == "a"


def test_a_b05_reject_comparison_chain():
    source = "SELECT * FROM t WHERE a<1<2;"
    with pytest.raises(SyntaxError) as caught:
        parse_one(source)
    assert caught.value.code == "CHAINED_COMPARISON"
    assert caught.value.span.start == position_at(source, source.rindex("<"))
    assert caught.value.span.start.column == 26


def test_a_b05_missing_operand():
    source = "SELECT * FROM t WHERE a=1 AND;"
    with pytest.raises(SyntaxError) as caught:
        parse_one(source)
    assert caught.value.span.start == position_at(source, source.index(";"))
    assert caught.value.span.start.column == 30
    assert set(caught.value.context["expected"]) >= {"IDENTIFIER", "INTEGER", "STRING", "LEFT_PAREN", "NOT"}
    assert caught.value.context["actual"] == "SEMICOLON"


@pytest.mark.parametrize("operator,normalized", [
    ("=", "="), ("!=", "!="), ("<", "<"), ("<=", "<="),
    (">", ">"), (">=", ">="), ("==", "="), ("<>", "!="),
])
def test_a_b05_all_comparisons(operator, normalized):
    assert shape(parse_one(f"SELECT * FROM t WHERE a {operator} 2").where) == (normalized, "a", 2)


def test_a_b05_left_associativity_and_parenthesis_override():
    assert shape(parse_one("SELECT * FROM t WHERE a OR b OR c").where) == ("OR", ("OR", "a", "b"), "c")
    assert shape(parse_one("SELECT * FROM t WHERE (a OR b) AND c").where) == ("AND", ("OR", "a", "b"), "c")
    assert shape(parse_one("SELECT * FROM t WHERE NOT a=1 AND b=2").where) == ("AND", ("NOT", ("=", "a", 1)), ("=", "b", 2))


@pytest.mark.parametrize("expression", ["()", "a=", "a=NOT b", "NOT", "(a=1", "a=1)", "a+2", "-a", "NULL"])
def test_a_b05_invalid_expressions(expression):
    with pytest.raises(SyntaxError) as caught:
        parse_one("SELECT * FROM t WHERE " + expression)
    assert caught.value.stage == "SYNTAX"
    assert caught.value.context["expected"]


def test_a_b05_types_are_not_checked_here():
    assert shape(parse_one("SELECT * FROM t WHERE age='old'").where) == ("=", "age", "old")
    assert shape(parse_one("SELECT * FROM t WHERE 1 AND 3.14").where) == ("AND", 1, 3.14)


def test_a_b05_nested_resource_limit_is_located():
    source = "SELECT * FROM t WHERE " + "(" * 65 + "id=1" + ")" * 65
    with pytest.raises(SyntaxError) as caught:
        parse_one(source)
    assert caught.value.code == "NESTING_LIMIT"
    assert caught.value.span.start.offset == source.index("(") + 64
    # 限制之内确实可解析，边界不是未处理的 RecursionError。
    assert parse_one("SELECT * FROM t WHERE " + "(" * 64 + "id=1" + ")" * 64).where.op == "="


def test_a_b05_parser_token_preconditions():
    with pytest.raises(ValueError, match="EOF"):
        Parser([])
    with pytest.raises(ValueError, match="末尾"):
        Parser(Lexer("").tokenize() * 2)
