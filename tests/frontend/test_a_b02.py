import pytest

from minidb.contracts.errors import LexicalError
from minidb.contracts.source import Position
from minidb.contracts.tokens import TokenKind as K
from minidb.frontend.lexer import Lexer
from .helpers import position_at


def test_a_b02_number_and_operator_tokens():
    tokens = Lexer("20 3.14 + - * / >= <= != == <>").tokenize()
    assert [t.kind for t in tokens] == [K.INTEGER, K.FLOAT, K.PLUS, K.MINUS, K.STAR,
        K.SLASH, K.GE, K.LE, K.NE, K.EQ_ALIAS, K.NE_ALIAS, K.EOF]
    assert [t.value for t in tokens[:2]] == [20, 3.14]
    assert [t.value for t in tokens[-3:-1]] == ["=", "!="]
    assert [t.lexeme for t in tokens[-3:-1]] == ["==", "<>"]


def test_a_b02_string_escape():
    tokens = Lexer("'Tom''s book' '张三' ''").tokenize()
    assert [t.value for t in tokens[:-1]] == ["Tom's book", "张三", ""]
    assert tokens[0].lexeme == "'Tom''s book'"


def test_a_b02_comment_positions():
    source = "-- x\nSELECT/*中文\n*/ name;"
    tokens = Lexer(source).tokenize()
    assert [t.kind for t in tokens] == [K.SELECT, K.IDENTIFIER, K.SEMICOLON, K.EOF]
    assert tokens[0].span.start == Position(5, 2, 1)
    assert tokens[1].span.start == Position(19, 3, 4)


@pytest.mark.parametrize("source,code", [("'abc", "UNTERMINATED_STRING"), ("/*abc", "UNTERMINATED_COMMENT")])
def test_a_b02_unterminated(source, code):
    with pytest.raises(LexicalError) as caught:
        Lexer(source).tokenize()
    assert caught.value.code == code
    assert caught.value.span.start == Position(0, 1, 1)
    assert caught.value.span.end.offset == len(source)


@pytest.mark.parametrize("source", ["1.", "1..2", "12abc", "1.2.3", "1e3", "1e-3", "3.14x"])
def test_a_b02_bad_number(source):
    with pytest.raises(LexicalError) as caught:
        Lexer(source).tokenize()
    assert caught.value.code == "INVALID_NUMBER"
    assert caught.value.span.start == Position(0, 1, 1)
    assert "非法数字" in caught.value.message


def test_a_b02_multiline_string_and_comment_states():
    source = "'张\r\n三--/*x*/''好'/* ' */\r\nname"
    tokens = Lexer(source).tokenize()
    assert tokens[0].value == "张\r\n三--/*x*/'好"
    assert tokens[0].lexeme == "'张\r\n三--/*x*/''好'"
    assert tokens[1].value == "name"
    assert tokens[1].span.start == position_at(source, source.index("name"))
    assert tokens[-1].span.start == position_at(source, len(source))


def test_a_b02_negative_boundary_is_two_tokens():
    tokens = Lexer("-2147483648").tokenize()
    assert [t.kind for t in tokens] == [K.MINUS, K.INTEGER, K.EOF]
    assert tokens[1].value == 2147483648


def test_a_b02_non_nested_block_comment():
    tokens = Lexer("/* outer /* inner */ id").tokenize()
    assert [t.kind for t in tokens] == [K.IDENTIFIER, K.EOF]
    assert tokens[0].value == "id"


def test_a_b02_large_integer_does_not_leak_python_value_error():
    token = Lexer("1" + "0" * 5000).tokenize()[0]
    assert token.value == 10 ** 5000


def test_a_b02_nonfinite_float_diagnostic():
    with pytest.raises(LexicalError) as caught:
        Lexer("9" * 400 + ".1").tokenize()
    assert caught.value.code == "INVALID_NUMBER"
    assert caught.value.span.end.offset == 402
