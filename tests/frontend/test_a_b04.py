import pytest

from minidb.contracts.ast import Identifier
from minidb.contracts.errors import SyntaxError
from .helpers import parse_one, position_at, shape


def test_a_b04_select_star():
    stmt = parse_one("SELECT * FROM Student;")
    assert stmt.table.name == "student"
    assert stmt.columns is None
    assert stmt.where is None


def test_a_b04_projection_order():
    stmt = parse_one("SELECT name,id,name FROM student;")
    assert tuple(x.name for x in stmt.columns) == ("name", "id", "name")
    assert all(isinstance(x, Identifier) for x in stmt.columns)
    assert len({x.span.start.offset for x in stmt.columns}) == 3


def test_a_b04_delete_optional_where():
    assert parse_one("DELETE FROM student;").where is None
    assert shape(parse_one("DELETE FROM student WHERE id=1;").where) == ("=", "id", 1)


def test_a_b04_missing_from():
    source = "SELECT id student;"
    with pytest.raises(SyntaxError) as caught:
        parse_one(source)
    assert caught.value.span.start == position_at(source, source.index("student"))
    assert "FROM" in caught.value.context["expected"]
    assert caught.value.context["actual"] == "IDENTIFIER"


@pytest.mark.parametrize("source,unexpected", [
    ("SELECT *,id FROM t;", ","),
    ("SELECT id FROM t ORDER BY id;", "ORDER"),
    ("SELECT id FROM t LIMIT 1;", "LIMIT"),
    ("SELECT id FROM t alias;", "alias"),
    ("SELECT id FROM t JOIN u ON id=1;", "JOIN"),
    ("SELECT id+1 FROM t;", "+"),
    ("DELETE t;", "t"),
    ("SELECT FROM t;", "FROM"),
])
def test_a_b04_reject_extra_syntax(source, unexpected):
    with pytest.raises(SyntaxError) as caught:
        parse_one(source)
    assert caught.value.context["lexeme"] == unexpected


def test_a_b04_unknown_column_is_preserved():
    stmt = parse_one("SELECT unknown FROM missing WHERE no_such_column='x'")
    assert stmt.columns[0].name == "unknown"
    assert shape(stmt.where) == ("=", "no_such_column", "x")
