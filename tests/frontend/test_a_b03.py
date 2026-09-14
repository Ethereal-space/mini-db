import pytest

from minidb.contracts.ast import CreateTableStmt, Literal
from minidb.contracts.errors import SyntaxError
from .helpers import parse_one, position_at


def test_a_b03_create_structure():
    source = "CREATE TABLE Student(id INT,name VARCHAR);"
    stmt = parse_one(source)
    assert isinstance(stmt, CreateTableStmt)
    assert stmt.name.name == "student"
    assert [(c.name.name, c.dtype_name) for c in stmt.columns] == [("id", "INT"), ("name", "VARCHAR")]
    assert stmt.span.end.offset == source.index(")") + 1
    assert source[stmt.name.span.start.offset:stmt.name.span.end.offset] == "Student"


def test_a_b03_insert_column_order():
    stmt = parse_one("INSERT INTO student(name,id) VALUES('张三',1);")
    assert tuple(x.name for x in stmt.columns) == ("name", "id")
    assert tuple(v.value for v in stmt.values) == ("张三", 1)
    assert all(isinstance(v, Literal) for v in stmt.values)


def test_a_b03_literal_boundary():
    source = "INSERT INTO t VALUES(-2147483648,3.14);"
    stmt = parse_one(source)
    assert stmt.columns is None
    assert tuple(v.value for v in stmt.values) == (-2147483648, 3.14)
    negative = stmt.values[0]
    assert source[negative.span.start.offset:negative.span.end.offset] == "-2147483648"


def test_a_b03_missing_separator():
    source = "CREATE TABLE t(id INT name VARCHAR);"
    with pytest.raises(SyntaxError) as caught:
        parse_one(source)
    assert caught.value.span.start == position_at(source, source.index("name"))
    assert set(caught.value.context["expected"]) == {"COMMA", "RPAREN"}


def test_a_b03_semantic_boundary():
    stmt = parse_one("CREATE TABLE t(id INT,id VARCHAR);")
    assert [c.name.name for c in stmt.columns] == ["id", "id"]
    with pytest.raises(SyntaxError) as caught:
        parse_one("CREATE TABLE t();")
    assert "IDENTIFIER" in caught.value.context["expected"]


@pytest.mark.parametrize("source", [
    "CREATE TABLE t(id FLOAT)", "CREATE TABLE t(id VARCHAR(20))",
    "CREATE TABLE t(id INT,)", "INSERT INTO t() VALUES(1)",
    "INSERT INTO t VALUES()", "INSERT INTO t VALUES(1,)",
    "INSERT INTO t VALUES(1),(2)", "INSERT INTO t VALUES(1+2)",
    "INSERT INTO t VALUES(-3.14)", "INSERT INTO t VALUES(+1)",
    "INSERT INTO t VALUES(NULL)", "CREATE TABLE t(id INT PRIMARY KEY)",
])
def test_a_b03_reject_noncore_syntax(source):
    with pytest.raises(SyntaxError) as caught:
        parse_one(source)
    assert caught.value.stage == "SYNTAX"
    assert caught.value.span is not None
    assert caught.value.context["expected"]


def test_a_b03_does_not_check_schema_or_value_limits():
    stmt = parse_one("INSERT INTO unknown(x,x) VALUES(2147483648,'" + "张" * 300 + "');")
    assert [c.name for c in stmt.columns] == ["x", "x"]
    assert stmt.values[0].value == 2147483648
    assert stmt.values[1].value == "张" * 300
