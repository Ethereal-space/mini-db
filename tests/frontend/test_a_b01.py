import pytest

from minidb.contracts.errors import LexicalError
from minidb.contracts.source import Position
from minidb.contracts.tokens import TokenKind as K
from minidb.frontend.lexer import Lexer


def test_a_b01_keyword_identifier():
    tokens = Lexer("SeLeCt name FROM Student").tokenize()
    assert [t.kind for t in tokens] == [K.SELECT, K.IDENTIFIER, K.FROM, K.IDENTIFIER, K.EOF]
    assert [t.lexeme for t in tokens] == ["SeLeCt", "name", "FROM", "Student", ""]
    assert [t.value for t in tokens] == ["select", "name", "from", "student", None]


def test_a_b01_positions():
    tokens = Lexer("SELECT\r\n\tname").tokenize()
    assert tokens[1].span.start == Position(9, 2, 2)
    assert tokens[1].span.end == Position(13, 2, 6)
    assert tokens[-1].span.start == tokens[-1].span.end == Position(13, 2, 6)


@pytest.mark.parametrize("source,position", [("", Position(0, 1, 1)), ("   ", Position(3, 1, 4))])
def test_a_b01_empty(source, position):
    tokens = Lexer(source).tokenize()
    assert len(tokens) == 1
    assert tokens[0].kind == K.EOF
    assert tokens[0].span.start == tokens[0].span.end == position


def test_a_b01_identifier_boundary():
    tokens = Lexer("select_x _a A1").tokenize()
    assert [t.kind for t in tokens[:-1]] == [K.IDENTIFIER] * 3
    assert [t.value for t in tokens[:-1]] == ["select_x", "_a", "a1"]


def test_a_b01_illegal_character():
    with pytest.raises(LexicalError) as caught:
        Lexer("SELECT @").tokenize()
    error = caught.value
    assert error.stage == "LEXICAL"
    assert error.span.start == Position(7, 1, 8)
    assert error.span.end == Position(8, 1, 9)
    assert "@" in error.message


def test_a_b01_identifier_limit():
    assert Lexer("a" * 64).tokenize()[0].value == "a" * 64
    with pytest.raises(LexicalError) as caught:
        Lexer("a" * 65).tokenize()
    assert caught.value.code == "IDENTIFIER_TOO_LONG"
    assert caught.value.span.start.offset == 0
    assert caught.value.span.end.offset == 65


@pytest.mark.parametrize("source", ["中文", "é", "😀", "Ａ", "١"])
def test_a_b01_ascii_only_identifiers(source):
    with pytest.raises(LexicalError) as caught:
        Lexer(source).tokenize()
    assert caught.value.span.start == Position(0, 1, 1)
    assert caught.value.span.end == Position(1, 1, 2)


@pytest.mark.parametrize("newline", ["\n", "\r", "\r\n"])
def test_a_b01_all_newlines(newline):
    tokens = Lexer("a" + newline + "b").tokenize()
    assert tokens[1].span.start == Position(1 + len(newline), 2, 1)
